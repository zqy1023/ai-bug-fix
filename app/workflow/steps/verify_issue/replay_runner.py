"""Response 重放执行器：在隔离环境中执行 gather main.py 并验证解析结果。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


class ReplayResult:
    """重放执行结果。"""

    def __init__(
        self,
        success: bool,
        output: str = "",
        error: str = "",
        exception_type: str = "",
        traceback: str = "",
    ) -> None:
        self.success = success
        self.output = output
        self.error = error
        self.exception_type = exception_type
        self.traceback = traceback

    def __repr__(self) -> str:
        if self.success:
            return f"ReplayResult(success=True, output={self.output[:100]}...)"
        return f"ReplayResult(success=False, error={self.error}, exception_type={self.exception_type})"


def run_replay(
    *,
    carrier: str,
    req_json: str,
    response_json: str,
    track_config_json: str,
    gathers_root: Path,
    timeout_seconds: int = 30,
) -> ReplayResult:
    """执行 response 重放验证。

    参数:
        carrier: 运输商代码 (如 "GLS")
        req_json: 请求 JSON 字符串
        response_json: 响应 JSON 字符串
        track_config_json: track_config JSON 字符串
        gathers_root: gathers 代码根目录
        timeout_seconds: 执行超时时间 (秒)

    返回:
        ReplayResult: 重放执行结果

    工作原理:
        1. 生成临时重放脚本 (注入 response)
        2. 在独立 subprocess 中执行
        3. 捕获标准输出、异常和 traceback
        4. 解析结果判断是否成功
    """
    gather_path = gathers_root / carrier / "main.py"
    if not gather_path.exists():
        return ReplayResult(
            success=False,
            error=f"Gather 文件不存在: {gather_path}",
        )

    # 生成临时重放脚本
    script_content = _generate_replay_script(
        carrier=carrier,
        req_json=req_json,
        response_json=response_json,
        track_config_json=track_config_json,
        gather_main_path=str(gather_path),
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
        tmp.write(script_content)
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [sys.executable, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(gathers_root.parent),  # 在 gathers 的父目录执行
        )

        # 解析输出
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 0:
            # 成功执行，解析输出
            try:
                output_data = json.loads(stdout)
                return ReplayResult(
                    success=True,
                    output=json.dumps(output_data, ensure_ascii=False, indent=2),
                )
            except json.JSONDecodeError:
                return ReplayResult(
                    success=False,
                    error="重放脚本输出不是有效的 JSON",
                    output=stdout,
                )
        else:
            # 执行失败，解析错误
            error_info = _parse_error_from_stderr(stderr)
            return ReplayResult(
                success=False,
                error=error_info.get("error", stderr),
                exception_type=error_info.get("exception_type", ""),
                traceback=error_info.get("traceback", stderr),
            )

    except subprocess.TimeoutExpired:
        return ReplayResult(
            success=False,
            error=f"重放执行超时 ({timeout_seconds}s)",
        )
    except Exception as e:
        return ReplayResult(
            success=False,
            error=f"重放执行异常: {e}",
        )
    finally:
        # 清理临时文件
        try:
            tmp_path.unlink()
        except Exception:
            pass


def _generate_replay_script(
    *,
    carrier: str,
    req_json: str,
    response_json: str,
    track_config_json: str,
    gather_main_path: str,
) -> str:
    """生成重放脚本，注入 response 并执行 main。"""
    # 转义 JSON 字符串中的引号和反斜杠
    req_escaped = req_json.replace("\\", "\\\\").replace('"', '\\"')
    response_escaped = response_json.replace("\\", "\\\\").replace('"', '\\"')
    track_config_escaped = track_config_json.replace("\\", "\\\\").replace('"', '\\"')

    return f'''"""自动生成的 Response 重放脚本"""
import json
import sys
import traceback
from pathlib import Path

# 动态导入 gather main
sys.path.insert(0, str(Path("{gather_main_path}").parent.parent))

try:
    from server_dev import main
    from framework.model.track_model import TrackRequestItemModel, TrackModel, TrackRequestConfigModel

    # 解析输入数据
    req_data = json.loads("""{req_escaped}""")
    response_data = """{response_escaped}"""
    track_config_data = json.loads("""{track_config_escaped}""")

    # 构造请求
    reqs = [TrackRequestItemModel(
        number=req_data.get("number", ""),
        param=req_data.get("param", ""),
        language=req_data.get("language", ""),
        gather_channel=req_data.get("gather_channel", "api"),
        user_token=req_data.get("user_token", "{{\\"app\\":false}}"),
    )]

    track_config = TrackRequestConfigModel(
        proxy=track_config_data.get("proxy", ""),
        timeout=track_config_data.get("timeout", 60000),
    )

    model = TrackModel(
        carrier="{carrier}",
        requests=reqs,
        track_config=track_config,
        response=response_data,
    )

    # 执行 main
    result = main(model)

    # 输出结果 (JSON 格式)
    print(json.dumps({{
        "success": True,
        "result": result.model_dump() if hasattr(result, "model_dump") else str(result),
    }}, ensure_ascii=False))

except Exception as e:
    # 输出错误 (JSON 格式)
    print(json.dumps({{
        "success": False,
        "exception_type": type(e).__name__,
        "error": str(e),
        "traceback": traceback.format_exc(),
    }}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)
'''


def _parse_error_from_stderr(stderr: str) -> dict[str, str]:
    """从 stderr 解析错误信息。"""
    try:
        error_data = json.loads(stderr)
        return error_data
    except json.JSONDecodeError:
        # stderr 不是 JSON，直接返回原始错误
        return {"error": stderr, "exception_type": "", "traceback": stderr}

