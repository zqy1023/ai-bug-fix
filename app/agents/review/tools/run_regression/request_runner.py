"""用日志请求执行 server_dev.main，读取真实业务返回，禁止注入录制响应。"""

from __future__ import annotations

import json
import pprint
import re
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from app.infrastructure.commands.runner import run_fixed_commands
from app.agents.review.tools.verify_issue.replay_model import extract_track_config
from app.agents.review.tools.verify_issue.replay_runner import resolve_interpreter
from app.agents.review.tools.verify_issue.semantic_compare import ReplayOutcome

_RETURN = "__AI_REQUEST_MAIN_RETURN__="

# 业务 track_config 缺省超时（毫秒）。与固定命令执行器的子进程超时兜底对齐：
# 没有该兜底时业务框架可能永远等待，run_fixed_commands 的进程级超时无法区分
# "业务在等"和"业务死了"。
_DEFAULT_REPLAY_TIMEOUT_MS = 12_000_000


def make_request_script(carrier: str, record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """把日志参数写成可核对的固定入口脚本；repr 保证日志只能成为数据。"""
    raw = record.get("req") or {}
    raw = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(raw, dict):
        raise TypeError("日志 req 必须是 JSON 对象")
    requests = raw.get("requests", [raw])
    if not isinstance(requests, list) or not requests or any(
        not isinstance(item, dict) or not str(item.get("number") or "").strip()
        for item in requests
    ):
        raise ValueError("日志请求缺少有效单号，停止请求回归")
    if raw.get("carrier") and raw["carrier"] != carrier:
        raise ValueError("日志 carrier 与本轮运输商不一致，停止请求回归")
    config = extract_track_config(record)
    config.setdefault("timeout", _DEFAULT_REPLAY_TIMEOUT_MS)
    channel = config.pop("proxy_channel_model", None)
    if channel is not None and not isinstance(channel, dict):
        raise ValueError("proxy_channel_model 必须是 JSON 对象")
    def literal(value: Any) -> str:
        """Python 字面量转义，不把日志内容解释成代码。"""
        return pprint.pformat(value, width=100, sort_dicts=False)
    request_lines = ",\n    ".join(f"TrackRequestItemModel(**{literal(item)})" for item in requests)
    channel_line = f"\n    proxy_channel_model=ProxyChannelModel(**{literal(channel)})," if channel else ""
    script = f'''import json
import importlib.util
from server_dev import main
from framework.models import TrackModel, TrackRequestItemModel, TrackRequestConfigModel, ProxyChannelModel

_spec = importlib.util.spec_from_file_location("ai_business_observation", {str(Path(__file__).resolve().parent.parent / 'verify_issue/business_observation.py')!r})
_observer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_observer)

reqs = [
    {request_lines}
]
track_config = TrackRequestConfigModel(
    **{literal(config)},{channel_line}
)
model = TrackModel(carrier={carrier!r}, requests=reqs, track_config=track_config)
with _observer.observe_business_errors() as business_observation:
    res = main(model)
# 当前 main 只打印 result，不返回对象；None 不代表业务失败。
returned = res.model_dump(mode="json") if hasattr(res, "model_dump") else res
effective_requests = [item.model_dump(mode="json", exclude_none=True, exclude_defaults=True) for item in reqs]
print({_RETURN!r} + json.dumps({{"returned": returned, "response": model.response, "requests": effective_requests, "business_observation": business_observation}}, ensure_ascii=False, default=str))
'''
    return script, {"carrier": carrier, "requests": requests,
                    "track_config": {**config, **({"proxy_channel_model": channel} if channel else {})}}


def read_main_result(stdout: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """main 无返回值时读 result: 后的 JSON，拒绝无结果及多义结果。"""
    frames = [line[len(_RETURN):] for line in stdout.splitlines() if line.startswith(_RETURN)]
    if len(frames) != 1:
        raise ValueError("server_dev.main 未正常完成，缺少唯一的返回记录")
    frame = json.loads(frames[0])
    printed = []
    for match in re.finditer(r"(?m)^\s*result:\s*\r?\n", stdout):
        value, _ = json.JSONDecoder().raw_decode(stdout[match.end():].lstrip())
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            printed.append(value)
    returned = frame.get("returned")
    if isinstance(returned, dict) and isinstance(returned.get("items"), list):
        if printed and (len(printed) != 1 or printed[0] != returned):
            raise ValueError("main 返回对象与打印结果不一致，无法确认业务结果")
        return returned, frame
    if len(printed) != 1:
        raise ValueError("main 未打印唯一有效的 result 业务结果")
    return printed[0], frame


def run_request_regression(
    project: Path, record: dict[str, Any], *, carrier: str,
    python_executable: str, timeout_seconds: int,
) -> dict[str, Any]:
    """在候选代码隔离目录发真实请求，进程成功和业务成功分别记录。"""
    evidence: dict[str, Any] = {"entrypoint": "server_dev.main", "status": "unverified", "passed": False}
    try:
        script, inputs = make_request_script(carrier, record)
        address = dotenv_values(project / ".env").get("proxy_manager_address")
        if address:
            # Git 版 main 的初始化不读取此 env；只适配子进程中的初始化钩子，
            # 保留 main 的完整调用，不写 server_dev 或框架源码。
            bootstrap = f'''from pathlib import Path
import os
from dotenv import dotenv_values
os.environ["proxy_manager_address"] = dotenv_values(Path.cwd() / ".env")["proxy_manager_address"]
import server_dev
from yq_common.proxy_forward_utils.proxy_forward_config import proxy_forward_config
_original_override_config = server_dev.override_config
def _regression_override_config():
    _original_override_config()
    proxy_forward_config._proxy_manager_address = os.environ["proxy_manager_address"]
    if proxy_forward_config.proxy_manager_address() != {address!r}:
        raise RuntimeError("回归代理地址未生效，停止业务请求")
    print("__AI_REQUEST_PROXY__=" + {address!r}, flush=True)
server_dev.override_config = _regression_override_config
'''
            script = bootstrap + script
            evidence["environment"] = {"source": ".env", "proxy_manager_address": address}
        evidence.update(script=script, input=inputs)
        interpreter = resolve_interpreter(python_executable)
        # -c 保持业务项目 cwd 为首个导入路径；完整脚本归档，不改业务仓库文件。
        command = [interpreter, "-c", script]
        execution = run_fixed_commands(project, [command], timeout_seconds=timeout_seconds)
        evidence["commands"] = execution["results"]
        if not execution["passed"]:
            evidence["status"] = "unverified"
            evidence["reason"] = (f"server_dev.main 执行超时 ({timeout_seconds}s)，未取得完整业务结果"
                                  if execution["results"][0]["returncode"] is None else
                                  "server_dev.main 进程执行失败，查看本次标准输出和错误堆栈；尚不能确认业务结果")
            return evidence
        result, frame = read_main_result(execution["results"][0]["stdout"])
        if address and f"__AI_REQUEST_PROXY__={address}" not in execution["results"][0]["stdout"].splitlines():
            raise ValueError("缺少代理地址生效证据，无法确认回归环境")
        evidence.update(result=result, response=frame.get("response"), effective_requests=frame.get("requests"))
        observation = frame.get("business_observation") or {}
        evidence["business_observation"] = observation
        outcome = ReplayOutcome.from_result(result)
        if not outcome.executed:
            raise ValueError("main 返回空或无效的业务 items，无法验证请求结果")
        evidence["items"] = [item.summary() for item in outcome.items]
        expected_requests = frame.get("requests")
        if not isinstance(expected_requests, list) or len(expected_requests) != len(inputs["requests"]):
            raise ValueError("缺少模型实际请求项，无法核对返回结果")
        expected = Counter(json.dumps(item, sort_keys=True, ensure_ascii=False) for item in expected_requests)
        # exclude_defaults 会省略默认字段；按原请求显式字段匹配，重复单号也计数。
        actual = Counter()
        remaining = list(expected_requests)
        for item in result.get("items") or []:
            request = item.get("request_item") or {}
            matched = next((value for value in remaining if all(request.get(key) == val
                           or (val is None and key not in request) for key, val in value.items())), None)
            if matched is None:
                raise ValueError("业务结果请求项与日志请求不一致")
            remaining.remove(matched)
            actual[json.dumps(matched, sort_keys=True, ensure_ascii=False)] += 1
        if actual != expected:
            raise ValueError("业务结果缺少日志请求项")
        passed = outcome.executed and bool(outcome.items) and all(
            item.succeeded or item.error_type == "NotFound" for item in outcome.items
        )
        uncertain = bool(outcome.items) and all(item.error_type in {"Succeed", "NotFound", "WebError", "ServiceError", "TimeoutError"}
                                               for item in outcome.items) and not passed
        timeout_note = (
            f"；业务配置时限 {inputs['track_config']['timeout']}ms，框架记录耗时 {result.get('cost', '未记录')}ms；"
            "本次请求超时，未取得完整业务结果，不能据此判定原异常修复无效"
            if any(item.error_type == "TimeoutError" for item in outcome.items) else ""
        )
        evidence.update(passed=passed, status="verified" if passed else "unverified" if uncertain else "failed",
                        reason="真实请求返回有效业务结果；无单与轨迹条数按逐单实际分类展示，不将无单视为请求执行失败" if passed else
                        "真实请求未通过：" + "; ".join(f"{item.request_number}: {item.error_type} {item.error_msg}"
                                                    for item in outcome.items) + timeout_note)
        business_errors = [error for error in observation.get("errors") or [] if error.get("business_related", True)]
        # 框架可能在记录 formatter 错误后仍返回 Succeed，不能把它提升为业务通过。
        if passed and business_errors:
            evidence.update(passed=False, status="failed", reason="请求返回 Succeed，但框架记录业务错误：" +
                            "；".join(str(item.get("category") or item.get("message") or "未分类错误")
                                     for item in business_errors))
    except (ValueError, TypeError, KeyError, OSError) as error:
        evidence["reason"] = str(error)
    return evidence
