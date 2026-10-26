"""重放结果与原始期望结果的语义比较。

比较对象是同一输入下的两次重放结论：基线（未修复代码）与修复后（AI 改动的代码）。
原始期望结果来自问题记录里的原始异常文本。比较只做语义层面的判断：
错误是否复现、错误是否被消除、轨迹结果是否发生漂移。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

# 事件比较只看时间与状态：运输商文案是上游原文，实时重放时可能自然漂移。
_TOKEN = re.compile(r"[a-z_]{4,}|[\u3400-\u9fff]+")
_PROXY_FAILURE = re.compile(
    r"proxyerror|proxyconnectionerror|cannot connect to proxy|unable to connect to proxy|"
    r"failed to connect to (?:the )?proxy|could not resolve proxy|"
    r"proxy (?:authentication required|connection (?:failed|refused|timed out))|"
    r"tunnel connection failed|connect tunnel failed|"
    r"(?:无法连接|连接失败|连接被拒绝|认证失败|鉴权失败|连接超时).{0,12}代理|"
    r"代理.{0,12}(?:无法连接|连接失败|连接被拒绝|认证失败|鉴权失败|连接超时)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReplayOutcome:
    """一次重放的可比较结论。"""

    # executed：重放是否跑出了结果。为 False 表示重放自身失败（异常/超时）。
    executed: bool = False
    error_type: str = ""
    error_msg: str = ""
    succeeded: bool = False
    sub_status: str = ""
    events: tuple[dict[str, Any], ...] = ()
    request_key: str = ""
    request_number: str = ""
    items: tuple[ReplayOutcome, ...] = ()
    parse_errors: tuple[dict[str, Any], ...] = ()
    observation: dict[str, Any] | None = None
    business_errors: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> ReplayOutcome:
        """从 TrackResponseModel 的 dump 里取出可比较的结论。"""
        items = result.get("items")
        if (
            not isinstance(items, list)
            or not items
            or any(not isinstance(item, dict) for item in items)
        ):
            return cls.from_failure("重放返回空或无效的 items", "InvalidReplayResult")
        captured = result.get("replay_parse_errors") or []
        business_errors = tuple(error for error in (result.get("replay_business_observation") or {}).get("errors") or []
                                if error.get("business_related", True))
        # 单请求可精确关联原异常；批量不把某一个请求的异常归给其他请求。
        original = captured[0] if len(items) == 1 and len(captured) == 1 else {}
        message = str(result.get("error_msg") or "")
        if original and not message:
            message = f"{original.get('type', '')}: {original.get('message', '')}"
        outcomes = tuple(
            replace(cls._from_item(item, message), business_errors=business_errors if len(items) == 1 else (), parse_errors=tuple(
                error for error in captured if isinstance(error, dict)
                and (len(items) == 1 or error.get("request_numbers") == [
                    str((item.get("request_item") or {}).get("number") or "")
                ])
            ))
            for item in items
        )
        return replace(
            outcomes[0],
            items=outcomes,
            succeeded=all(item.succeeded for item in outcomes),
            observation=result.get("replay_observation"),
            business_errors=business_errors,
        )

    @classmethod
    def _from_item(cls, first: dict[str, Any], error_msg: str) -> ReplayOutcome:
        error_type = str(first.get("error_type") or "")
        track_result = first.get("track_result")
        track_result = track_result if isinstance(track_result, dict) else {}
        events: list[dict[str, Any]] = []
        raw_events = track_result.get("track_result_events")
        for event in raw_events if isinstance(raw_events, list) else []:
            if not isinstance(event, dict):
                continue
            moment = event.get("datetime")
            events.append(
                {
                    "time": str(moment.get("datetime") or "")
                    if isinstance(moment, dict)
                    else "",
                    "status": str(
                        event.get("formated_sub_status")
                        or event.get("sub_status")
                        or ""
                    ),
                    "description": str(event.get("description") or ""),
                }
            )
        request = first.get("request_item")
        request = request if isinstance(request, dict) else {}
        return cls(
            executed=True,
            error_type=error_type,
            error_msg=error_msg,
            succeeded=error_type == "Succeed" and bool(track_result),
            sub_status=str(
                track_result.get("formated_sub_status")
                or track_result.get("sub_status")
                or ""
            ),
            events=tuple(events),
            request_key=json.dumps(request, sort_keys=True, ensure_ascii=False)
            if request
            else "",
            request_number=str(request.get("number") or "").strip(),
        )

    @classmethod
    def from_failure(cls, error: str, exception_type: str = "") -> ReplayOutcome:
        """重放自身失败（异常、超时、子进程错误）时的结论。"""
        return cls(
            executed=False, error_type=exception_type or "ReplayError", error_msg=error
        )

    def summary(self) -> dict[str, Any]:
        """写进报告的摘要。事件只保留前几条，避免报告被完整轨迹撑爆。"""
        return {
            "executed": self.executed,
            "error_type": self.error_type,
            "error_msg": self.error_msg[:1000],
            "succeeded": self.succeeded,
            "request_number": self.request_number,
            "sub_status": self.sub_status,
            "event_count": len(self.events),
            "events_preview": [
                {"time": event["time"], "status": event["status"]}
                for event in self.events[:5]
            ],
            "parse_errors": list(self.parse_errors),
            "observation": self.observation,
            "business_errors": list(self.business_errors),
        }


@dataclass(frozen=True)
class Comparison:
    """语义比较结论。"""

    # verdict：fixed / not_fixed / regression / new_error / not_reproduced /
    # environment / replay_failed / baseline_failed。
    verdict: str
    passed: bool
    # verified：结论是否成立。False 表示证据不足，只能转人工确认。
    verified: bool
    reason: str
    details: dict[str, Any]


def _tokens(text: str) -> set[str]:
    # 栈位置及 JSONDecodeError 的行列号不是错误语义。
    text = re.split(r"\bfile\s*:|\bfunction\s*:", text, maxsplit=1, flags=re.IGNORECASE)[0]
    text = re.sub(r"\b(?:[a-z_]\w*\.)+([A-Za-z_]\w*(?:Error|Exception)|Exception|Error)\b", r"\1", text)
    text = re.sub(r"\b(?:line|column|char)\s+\d+", "", text, flags=re.IGNORECASE)
    return set(_TOKEN.findall(text.lower()))


def _normalize(text: str) -> str:
    """无语义词时保留 Unicode 字母和数字，避免不同错误均变成空串。"""
    return "".join(character for character in text.casefold() if character.isalnum())


def errors_equivalent(left: str, right: str) -> bool:
    """两个错误文本是否语义等价。

    生产日志里的错误文本往往比重放得到的更长（可能带完整请求内容），
    所以用词集合的包含关系判断，而不是整串相等。
    """
    if not left.strip() or not right.strip():
        return False
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        normalized = _normalize(left)
        return bool(normalized) and normalized == _normalize(right)
    if (
        left_tokens == right_tokens
        or left_tokens <= right_tokens
        or right_tokens <= left_tokens
    ):
        return True
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= 0.5


def _same_error(left: ReplayOutcome, right: str) -> bool:
    """优先匹配真实异常类型及原报错位置，避免用框架分类猜原异常。"""
    for error in left.business_errors:
        message = str(error.get("message") or "")
        if not ((error.get("category") and str(error["category"]) in right) or errors_equivalent(message, right)):
            continue
        # 同类时间错误可能有多个字段；原日志给出 raw 时必须核对该值。
        raw = re.search(r'"raw"\s*:\s*("(?:[^"\\]|\\.)*")', right)
        if raw and str(json.loads(raw.group(1))) not in message:
            continue
        return True
    exception = re.search(r"\b([A-Za-z_]\w*(?:Error|Exception)|Exception|Error)\b", right)
    function = re.search(r"\bfunction\s*:\s*([\w.<>]+)", right, re.IGNORECASE)
    filename = re.search(r"\bfile\s*:\s*([^,\s]+)", right, re.IGNORECASE)
    if left.parse_errors:
        for error in left.parse_errors:
            if exception and error.get("type") != exception.group(1):
                continue
            frames = error.get("frames") or []
            if function or filename:
                if not any(
                    (not function or str(frame.get("function") or "").rsplit(".", 1)[-1]
                     == function.group(1).rsplit(".", 1)[-1])
                    and (not filename or str(frame.get("file") or "").replace("\\", "/").split("/")[-1]
                         == filename.group(1).replace("\\", "/").split("/")[-1])
                    for frame in frames
                ):
                    continue
            # 生产摘要可能只保留异常正文前缀；不要让其后完整 HTML 改变异常身份。
            expected_message = right[exception.end():].lstrip(" ,:") if exception else right
            expected_message = re.split(r"\b(?:file|function)\s*:", expected_message, maxsplit=1, flags=re.IGNORECASE)[0].strip().rstrip(",")
            actual_message = str(error.get("message") or "")
            if expected_message and (actual_message.startswith(expected_message) or errors_equivalent(actual_message, expected_message)):
                return True
        return False
    # 无原异常观测时，不能凭 ProcessError 或局部消息宣称已复现 Python 异常。
    return not exception and errors_equivalent(left.error_msg, right)


def _same_result(left: ReplayOutcome, right: ReplayOutcome) -> bool:
    """两条轨迹结果是否语义一致（事件条数、时间、状态、包裹状态）。"""
    if left.sub_status != right.sub_status:
        return False
    if len(left.events) != len(right.events):
        return False
    for left_event, right_event in zip(left.events, right.events):
        if left_event["time"] != right_event["time"]:
            return False
        if left_event["status"] != right_event["status"]:
            return False
    return True


def _result_diff(baseline: ReplayOutcome, fixed: ReplayOutcome) -> dict[str, Any]:
    """基线与修复后结果的差异明细。"""
    diff: dict[str, Any] = {
        "baseline_sub_status": baseline.sub_status,
        "fixed_sub_status": fixed.sub_status,
        "baseline_event_count": len(baseline.events),
        "fixed_event_count": len(fixed.events),
    }
    descriptions = [
        {
            "index": index,
            "baseline": left.get("description", ""),
            "fixed": right.get("description", ""),
        }
        for index, (left, right) in enumerate(zip(baseline.events, fixed.events))
        if left.get("description") != right.get("description")
    ]
    if descriptions:
        diff["description_diff"] = descriptions[:20]
    return diff


def _compare_single(
    *,
    baseline: ReplayOutcome | None,
    fixed: ReplayOutcome | None,
    original_error: str = "",
) -> Comparison:
    """比较基线重放与修复后重放，给出业务结论。"""
    details: dict[str, Any] = {
        "original_error": original_error[:2000],
        "baseline": baseline.summary() if baseline else None,
        "fixed": fixed.summary() if fixed else None,
    }
    if fixed is None or baseline is None:
        return Comparison(
            "not_reproduced",
            passed=False,
            verified=False,
            reason="缺少可比较的重放结果，无法做语义比较",
            details=details,
        )
    baseline_proxy = not baseline.succeeded and bool(
        _PROXY_FAILURE.search(f"{baseline.error_type}: {baseline.error_msg}")
    )
    fixed_proxy = not fixed.succeeded and bool(
        _PROXY_FAILURE.search(f"{fixed.error_type}: {fixed.error_msg}")
    )
    details["proxy_failure_evidence"] = {
        "baseline": baseline_proxy,
        "fixed": fixed_proxy,
    }
    if baseline_proxy and fixed_proxy:
        return Comparison(
            "environment",
            passed=False,
            verified=False,
            reason="基线与修复后均有代理连接或认证失败证据，本地无法使用线上代理，业务修复未验证",
            details=details,
        )
    if not fixed.executed:
        if (
            not baseline.executed
            and baseline.error_type == fixed.error_type
            and baseline.error_type
            in {"ModuleNotFoundError", "ImportError", "FileNotFoundError", "TimeoutExpired"}
            and _normalize(fixed.error_msg) == _normalize(baseline.error_msg)
        ):
            # 两侧都死在同一个地方：多半是重放环境本身的问题（缺依赖、代理不可用），
            # 不是业务代码的结论，只能转人工确认。
            return Comparison(
                "environment",
                passed=False,
                verified=False,
                reason=(
                    f"基线与修复后重放出现相同的执行失败，属于重放环境问题: "
                    f"{fixed.error_msg[:200] or fixed.error_type}"
                ),
                details=details,
            )
        return Comparison(
            "replay_failed",
            passed=False,
            verified=True,
            reason=f"修复后重放执行失败: {fixed.error_msg or fixed.error_type}",
            details=details,
        )
    if not baseline.executed:
        return Comparison(
            "baseline_failed",
            passed=False,
            verified=False,
            reason=f"基线重放执行失败，无法确认原始异常是否可复现: {baseline.error_msg or baseline.error_type}",
            details=details,
        )
    if fixed.business_errors:
        return Comparison("not_fixed" if _same_error(fixed, original_error) else "new_error", False, True,
                          "修复后框架仍记录业务错误，Succeed 不代表目标错误已消除", details)
    if baseline.succeeded and not baseline.business_errors and not fixed.succeeded:
        return Comparison(
            "regression",
            passed=False,
            verified=True,
            reason=f"基线成功，修复后返回错误: {fixed.error_type}: {fixed.error_msg[:200]}",
            details=details,
        )
    if (not baseline.succeeded or baseline.business_errors) and _same_error(baseline, original_error):
        if _same_error(fixed, original_error):
            return Comparison(
                "not_fixed", False, True, "修复后仍在原报错位置复现原异常，问题未修复", details,
            )
        # 基线复现了原始异常：本次修改必须把它消掉，且不能引入新错误。
        if fixed.succeeded:
            return Comparison(
                "fixed",
                passed=True,
                verified=True,
                reason=("基线重放复现原始问题，修复后返回 Succeed；"
                        + ("包含轨迹事件" if fixed.events else "无轨迹事件，须由审查核对无单/空结果的业务字段依据")),
                details=details,
            )
        if fixed.error_type == "ServiceError" and fixed.error_msg.strip() and not fixed.events:
            details["acceptance"] = {
                "kind": "exception_handling",
                "observed": "原 Python 异常消除，返回带原因的 ServiceError，未返回轨迹",
                "review_required": "审查实际分支及原因是否对应样本，排除吞异常、泛化兜底或错误无单判断",
            }
            return Comparison(
                "exception_handled", passed=True, verified=True,
                reason="基线复现原异常，修复后返回带明确原因的 ServiceError；已验证异常处理协议，业务归类依据由代码审查核对，不代表网站恢复。",
                details=details,
            )
        return Comparison(
            "new_error",
            passed=False,
            verified=True,
            reason=f"修复后重放出现与原始异常不同的错误: {fixed.error_type}: {fixed.error_msg[:200]}",
            details=details,
        )
    # 基线没有复现原始异常：无法把这次修改和原始问题绑定，只能给人工确认。
    if baseline.succeeded and fixed.succeeded:
        details["result_diff"] = _result_diff(baseline, fixed)
        if _same_result(baseline, fixed):
            return Comparison(
                "not_reproduced",
                passed=False,
                verified=False,
                reason="基线重放没有复现原始异常（基线本身即成功），无法确认本次修改是否修复了问题",
                details=details,
            )
        return Comparison(
            "regression",
            passed=False,
            verified=True,
            reason="基线重放成功，但修复后重放的轨迹结果发生语义变化，存在回归风险",
            details=details,
        )
    if not fixed.succeeded:
        details["result_diff"] = _result_diff(baseline, fixed)
        return Comparison(
            "not_reproduced",
            passed=False,
            verified=False,
            reason=(
                f"基线重放未复现原始异常（基线错误: {baseline.error_type}），"
                f"修复后框架返回: {fixed.error_type}；原始异常尚未确认复现，无法判断补丁效果"
            ),
            details=details,
        )
    return Comparison(
        "not_reproduced",
        passed=False,
        verified=False,
        reason="基线重放未复现原始异常，无法确认本次修改修复了原始问题",
        details=details,
    )


def compare(
    *,
    baseline: ReplayOutcome | None,
    fixed: ReplayOutcome | None,
    original_error: str = "",
    live: bool = False,
    expected_numbers: list[str] | None = None,
    captured_response: str = "",
) -> Comparison:
    """逐单比较；实时请求没有固定上游响应，不能据此作确定的业务结论。"""
    details: dict[str, Any] = {
        "original_error": original_error[:2000],
        "baseline": baseline.summary() if baseline else None,
        "fixed": fixed.summary() if fixed else None,
        "input_mode": "live" if live else "recorded",
    }
    if not live:
        import hashlib

        expected_hash = hashlib.sha256(captured_response.encode("utf-8")).hexdigest()
        observations = [value.observation if value else None for value in (baseline, fixed)]
        details["input_delivery"] = observations
        valid = bool(captured_response) and all(
            isinstance(observed, dict)
            and observed.get("schema_version") == 3
            and observed.get("input_delivered") is True
            and observed.get("network_attempts") == 0
            and (observed.get("expected_input") or {}).get("sha256") == expected_hash
            and bool(observed.get("parser_calls"))
            and all(call.get("input_matches_recording") is True
                    and call.get("business_entry") is True
                    and str(call.get("module") or "").startswith(("gathers.", "spiders."))
                    and "traced_process" not in str(call.get("entrypoint") or "")
                    and (call.get("input") or {}).get("sha256") == expected_hash
                    and call.get("source_sha256")
                    for call in observed["parser_calls"])
            for observed in observations
        )
        if not valid:
            return Comparison(
                "input_not_verified", False, False,
                "无法证明同一录制正文已到达两侧真实解析入口，或固定响应验证触发了网络请求；不得根据返回分类判断修复效果",
                details,
            )
    if fixed and fixed.business_errors and len(fixed.items) > 1:
        # 批量日志不能猜归属给某一单，但整批仍有业务错误，不能放行。
        return Comparison("new_error", False, True, "修复后批量运行仍记录框架业务错误，详见原始错误及调用栈", details)
    if baseline and fixed and baseline.items and fixed.items:
        left, right = baseline.items, fixed.items
        # 单条旧记录允许缺少 request_item；批量必须有身份，不能按返回顺序猜测。
        identities_available = all(item.request_key for item in (*left, *right))
        mismatch = len(left) != len(right) or (
            max(len(left), len(right)) > 1 and not identities_available
        )
        if identities_available:
            mismatch |= Counter(item.request_key for item in left) != Counter(
                item.request_key for item in right
            )
            if expected_numbers is not None:
                expected = Counter(expected_numbers)
                mismatch |= any(
                    Counter(item.request_number for item in side) != expected
                    for side in (left, right)
                )
        elif expected_numbers is not None:
            mismatch = True
        if mismatch:
            comparison = Comparison(
                "replay_failed",
                False,
                True,
                "重放返回的请求身份或数量不一致，无法完整验证全部单号",
                details,
            )
        else:
            remaining = list(right)
            pairs = []
            for before in left:
                index = next(
                    (
                        i
                        for i, after in enumerate(remaining)
                        if after.request_key == before.request_key
                    ),
                    0,
                )
                pairs.append((before, remaining.pop(index)))
            results = [
                _compare_single(baseline=b, fixed=f, original_error=original_error)
                for b, f in pairs
            ]
            details["items"] = [
                {
                    "request_number": before.request_number,
                    "verdict": value.verdict,
                    "passed": value.passed,
                    "verified": value.verified,
                    "reason": value.reason,
                    "details": value.details,
                }
                for (before, _), value in zip(pairs, results)
            ]
            failures = [
                value for value in results if value.verified and not value.passed
            ]
            uncertain = [
                value
                for (before, after), value in zip(pairs, results)
                if not value.verified
                and not (
                    before.succeeded and after.succeeded and _same_result(before, after)
                )
            ]
            if failures or uncertain:
                comparison = replace((failures or uncertain)[0], details=details)
            elif any(value.passed for value in results):
                comparison = Comparison(
                    "exception_handled" if any(value.verdict == "exception_handled" for value in results) else "fixed",
                    True,
                    True,
                    "；".join(dict.fromkeys(value.reason for value in results if value.passed)),
                    details,
                )
            else:
                comparison = replace(results[0], details=details)
    else:
        comparison = _compare_single(
            baseline=baseline, fixed=fixed, original_error=original_error,
        )
    if live and comparison.verdict != "environment":
        evidence = {
            **comparison.details,
            "input_mode": "live",
            "observed_verdict": comparison.verdict,
        }
        return Comparison(
            "not_reproduced",
            False,
            False,
            "实时重放的两次上游响应未固定，观察到的差异无法归因于代码修改，业务修复未验证",
            evidence,
        )
    return comparison


__all__ = ["Comparison", "ReplayOutcome", "compare", "errors_equivalent"]
