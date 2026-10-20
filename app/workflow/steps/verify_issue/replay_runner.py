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
    def __init__(self, success: bool, output: str = "", error: str = "", exception_type: str = "", traceback: str = "") -> None:
        self.success = success
        self.output = output
        self.error = error
        self.exception_type = exception_type
        self.traceback = traceback

    def __repr__(self) -> str:
        if self.success:
            return f"ReplayResult(success=True, output={self.output[:100]}...)"
        return f"ReplayResult(success=False, error={self.error}, exception_type={self.exception_type})"


_REPLAY_SCRIPT = r'''
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    gather_main_path = Path(payload["gather_main_path"]).resolve()
    sys.path.insert(0, str(gather_main_path.parent.parent))
    from server_dev import main
    # 业务仓库的模型定义在 framework/models.py；曾按旧目录 framework/model/track_model
    # 硬编码，仓库结构变化后所有重放都会先报 No module named 'framework.model'。
    from framework.models import TrackRequestItemModel, TrackModel, TrackRequestConfigModel

    req_data = payload["req"]
    response_data = payload["response"]
    track_config_data = payload["track_config"]
    reqs = [TrackRequestItemModel(
        number=req_data.get("number", ""),
        param=req_data.get("param", ""),
        language=req_data.get("language", ""),
        gather_channel=req_data.get("gather_channel", "api"),
        user_token=req_data.get("user_token", "{\"app\":false}"),
    )]
    track_config = TrackRequestConfigModel(
        proxy=track_config_data.get("proxy", ""),
        timeout=track_config_data.get("timeout", 60000),
    )
    model = TrackModel(
        carrier=payload["carrier"],
        requests=reqs,
        track_config=track_config,
        response=response_data,
    )
    result = main(model)
    print(json.dumps({
        "success": True,
        "result": result.model_dump() if hasattr(result, "model_dump") else str(result),
    }, ensure_ascii=False))
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


def run_replay(
    *,
    carrier: str,
    req_json: str,
    response_json: str,
    track_config_json: str,
    gathers_root: Path,
    timeout_seconds: int = settings.replay_timeout_seconds,
) -> ReplayResult:
    """执行 response 重放；carrier 路径和 JSON 输入均按数据处理，不插入源码。"""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", carrier) or carrier in {".", ".."}:
        return ReplayResult(False, error="carrier 名称包含非法路径字符")
    root = gathers_root.resolve()
    # 源码目录可能保留原始大小写（gathers/Airpak），按磁盘真实路径解析。
    carrier_dir = WorkspaceManager.find_child_dir(root, carrier)
    gather_path = (
        (carrier_dir / "main.py").resolve()
        if carrier_dir is not None
        else (root / carrier / "main.py").resolve()
    )
    try:
        gather_path.relative_to(root)
    except ValueError:
        return ReplayResult(False, error="carrier 路径越出 gathers 根目录")
    if not gather_path.is_file():
        return ReplayResult(False, error=f"Gather 文件不存在: {gather_path}")

    try:
        payload: dict[str, Any] = {
            "carrier": carrier,
            "req": json.loads(req_json),
            "response": response_json,
            "track_config": json.loads(track_config_json),
            "gather_main_path": str(gather_path),
        }
    except (json.JSONDecodeError, TypeError) as error:
        return ReplayResult(False, error="重放输入 JSON 无效", exception_type=type(error).__name__)

    with temporary_directory("ai-replay-") as directory:
        input_path = Path(directory) / "input.json"
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            with temporary_directory("ai-replay-pycache-") as pycache:
                result = subprocess.run(
                    [sys.executable, "-c", _REPLAY_SCRIPT, str(input_path)],
                    capture_output=True,
                    text=True,
                    timeout=max(1, int(timeout_seconds)),
                    cwd=str(root.parent),
                    check=False,
                    env={
                        **os.environ,
                        "PYTHONPYCACHEPREFIX": str(pycache),
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                )
        except subprocess.TimeoutExpired:
            return ReplayResult(
                False,
                error=f"重放执行超时 ({timeout_seconds}s)",
                exception_type="TimeoutExpired",
            )
        except OSError as error:
            return ReplayResult(False, error="无法启动重放子进程", exception_type=type(error).__name__)

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode != 0:
        try:
            error_data = json.loads(stderr)
        except json.JSONDecodeError:
            error_data = {"error": "重放子进程失败", "exception_type": "SubprocessError"}
        return ReplayResult(
            False,
            error=str(error_data.get("error") or "重放执行失败"),
            exception_type=str(error_data.get("exception_type") or ""),
            traceback=str(error_data.get("traceback") or ""),
        )

    # 允许被测代码向 stdout 打日志，读取最后一行结构化结果。
    for line in reversed(stdout.splitlines()):
        try:
            output_data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(output_data, dict) and output_data.get("success") is True:
            return ReplayResult(True, output=json.dumps(output_data, ensure_ascii=False, indent=2))
        if isinstance(output_data, dict) and output_data.get("success") is False:
            return ReplayResult(
                False,
                error=str(output_data.get("error") or "重放目标执行失败"),
                exception_type=str(output_data.get("exception_type") or ""),
                traceback=str(output_data.get("traceback") or ""),
            )
    return ReplayResult(False, error="重放脚本未输出有效的成功 JSON", output=stdout[-4000:])


__all__ = ["ReplayResult", "run_replay"]
