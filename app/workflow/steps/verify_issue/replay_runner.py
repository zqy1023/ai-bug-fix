"""Response 重放执行器：通过 JSON 文件传参，避免把日志内容拼接进 Python 源码。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.infrastructure.filesystem.safe_cleanup import temporary_directory
from app.infrastructure.filesystem.workspace import WorkspaceManager
from config.settings import settings


class ReplayResult:
    def __init__(
        self,
        success: bool,
        output: str = "",
        error: str = "",
        exception_type: str = "",
        traceback: str = "",
        result: dict[str, Any] | None = None,
        live: bool = False,
    ) -> None:
        self.success = success
        self.output = output
        self.error = error
        self.exception_type = exception_type
        self.traceback = traceback
        # result：TrackResponseModel 的结构化结果，是语义比较的唯一输入。
        self.result: dict[str, Any] = result or {}
        # live：没有录制的 response，重放真实发起了请求。
        self.live = live
        self.command: list[str] = []
        self.returncode: int | None = None

    def __repr__(self) -> str:
        if self.success:
            return f"ReplayResult(success=True, output={self.output[:100]}...)"
        return f"ReplayResult(success=False, error={self.error}, exception_type={self.exception_type})"


# 重放脚本在子进程里执行，直接产出 TrackResponseModel 的结构化结果。
# server_dev.main() 没有返回值（只 asyncio.run），拿不到结果就无法比较，
# 所以这里直接调用 TrackServer.track()。
_REPLAY_SCRIPT = r'''
import asyncio
import importlib.util
import json
import sys
from pathlib import Path


def load_model_builder(path):
    """按文件路径加载请求还原模块，不污染业务项目的 import 路径。

    业务项目根下也有 config.py，工具根一旦先入 sys.path 就会顶掉它。
    """
    spec = importlib.util.spec_from_file_location("ai_replay_model", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_track_model


def main():
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    gather_main_path = Path(payload["gather_main_path"]).resolve()
    sys.path.insert(0, str(gather_main_path.parent.parent))
    # server_dev 导入即完成 LogUtils/Prometheus 初始化。这里不调用它的
    # override_config：它会把状态规则换成联网加载的 MySqlLoader 并写死本地代理，
    # 与生产（FileLoader + py_rules_new.zip）不一致，重放结果不可比。
    import server_dev  # noqa: F401
    from framework.track_server import TrackServer

    build_track_model = load_model_builder(payload["model_path"])
    model = build_track_model(payload)
    response = asyncio.run(TrackServer().track(track_model=model))
    print(json.dumps({
        "success": True,
        "result": response.model_dump(mode="json"),
    }, ensure_ascii=False, default=str))


try:
    main()
except Exception as error:
    import traceback
    print(json.dumps({
        "success": False,
        "exception_type": type(error).__name__,
        "error": str(error)[:2000],
        "traceback": traceback.format_exc()[-4000:],
    }, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)
'''

# 请求还原模块的绝对路径：子进程按文件路径加载，不改业务项目的 import 路径。
_MODEL_PATH = Path(__file__).resolve().with_name("replay_model.py")

# 业务代码在业务项目自己的解释器里跑。工具虚拟环境通常没有 lxml/curl_cffi 这类
# 业务依赖，用它跑重放会在 import 阶段就失败，验证结果与业务代码无关。
# 解释器路径由配置给出（settings.replay_python / AI_REPAIR_REPLAY_PYTHON），
# 代码里不猜测解释器位置。
def _resolve_interpreter(python_executable: str) -> str:
    """取出重放用的解释器路径。

    没配置时退回当前解释器；配置了但路径不存在时直接报错，避免重放退化成
    用错环境的假结论。
    """
    configured = (python_executable or "").strip()
    if not configured:
        return sys.executable
    path = Path(configured)
    if not path.is_file():
        raise FileNotFoundError(f"重放解释器不存在: {configured}")
    return str(path.resolve())


def run_replay(
    *,
    carrier: str,
    req_json: str,
    response_json: str,
    track_config_json: str,
    gathers_root: Path,
    timeout_seconds: int = settings.replay_timeout_seconds,
    python_executable: str = settings.replay_python,
) -> ReplayResult:
    """执行 response 重放；carrier 路径和 JSON 输入均按数据处理，不插入源码。

    ``response_json`` 为空表示没有录制的响应，重放会真实发起一次请求。
    ``python_executable`` 是跑业务代码的解释器，必须装有业务依赖。
    """
    command: list[str] = []
    def finish(*args: Any, **kwargs: Any) -> ReplayResult:
        value = ReplayResult(*args, **kwargs)
        value.command = command
        value.returncode = getattr(locals_result[0], "returncode", None) if locals_result else None
        return value
    locals_result: list[Any] = []
    live = not response_json
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", carrier) or carrier in {".", ".."}:
        return finish(False, error="carrier 名称包含非法路径字符")
    root = gathers_root.resolve()
    # 源码目录可能保留原始大小写（gathers/Airpak），按磁盘真实路径解析。
    carrier_dir = WorkspaceManager.find_child_dir(root, carrier)
    legacy_dir = WorkspaceManager.find_carrier_dir(root.parent, "spiders", carrier)
    gather_path = (carrier_dir / "main.py").resolve() if carrier_dir is not None else None
    if gather_path is None or not gather_path.is_file():
        # server_dev 按注册机制选择旧插件；此路径仅用于定位项目入口，不导入虚构 main.py。
        if legacy_dir is None:
            return finish(False, error="新旧框架中均找不到重放目标")
        gather_path = root / carrier / "main.py"
    try:
        payload: dict[str, Any] = {
            "carrier": carrier,
            "req": json.loads(req_json),
            "response": response_json,
            "track_config": json.loads(track_config_json or "{}"),
            "gather_main_path": str(gather_path),
            "model_path": str(_MODEL_PATH),
        }
    except (json.JSONDecodeError, TypeError) as error:
        return finish(False, error="重放输入 JSON 无效", exception_type=type(error).__name__)

    with temporary_directory("ai-replay-") as directory:
        input_path = Path(directory) / "input.json"
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            interpreter = _resolve_interpreter(python_executable)
        except FileNotFoundError as error:
            return finish(False, error=str(error), exception_type="FileNotFoundError", live=live)
        try:
            with temporary_directory("ai-replay-pycache-") as pycache:
                command = [interpreter, "-c", _REPLAY_SCRIPT, str(input_path)]
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=max(1, int(timeout_seconds)),
                    cwd=str(root.parent),
                    check=False,
                    env={
                        **os.environ,
                        "PYTHONPYCACHEPREFIX": str(pycache),
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONIOENCODING": "utf-8",
                    },
                )
        except subprocess.TimeoutExpired:
            return finish(
                False,
                error=f"重放执行超时 ({timeout_seconds}s)",
                exception_type="TimeoutExpired",
                live=live,
            )
        except OSError as error:
            return finish(False, error="无法启动重放子进程", exception_type=type(error).__name__, live=live)

    locals_result.append(result)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode != 0:
        # 业务初始化也可能向 stderr 写日志；保留脚本最后一行中的真实异常证据。
        error_data = {"error": "重放子进程失败", "exception_type": "SubprocessError"}
        for line in reversed(stderr.splitlines()):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and candidate.get("success") is False:
                error_data = candidate
                break
        return finish(
            False,
            error=str(error_data.get("error") or "重放执行失败"),
            exception_type=str(error_data.get("exception_type") or ""),
            traceback=str(error_data.get("traceback") or ""),
            live=live,
        )

    # 允许被测代码向 stdout 打日志，读取最后一行结构化结果。
    for line in reversed(stdout.splitlines()):
        try:
            output_data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(output_data, dict) and output_data.get("success") is True:
            raw_result = output_data.get("result")
            return finish(
                True,
                output=json.dumps(output_data, ensure_ascii=False, indent=2),
                result=raw_result if isinstance(raw_result, dict) else {},
                live=live,
            )
        if isinstance(output_data, dict) and output_data.get("success") is False:
            return finish(
                False,
                error=str(output_data.get("error") or "重放目标执行失败"),
                exception_type=str(output_data.get("exception_type") or ""),
                traceback=str(output_data.get("traceback") or ""),
                live=live,
            )
    return finish(False, error="重放脚本未输出有效的成功 JSON", output=stdout[-4000:], live=live)


__all__ = ["ReplayResult", "run_replay"]
