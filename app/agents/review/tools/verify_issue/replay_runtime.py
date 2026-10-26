"""在平台子进程适配解析输入并观测真实框架，不改写业务仓库文件。"""

from __future__ import annotations

import hashlib
import inspect
import sys
import traceback
from pathlib import Path
from typing import Any


def fingerprint(value: Any) -> dict[str, Any]:
    """记录输入内容身份，避免把模型有 response 等同于解析器已收到。"""
    text = value if isinstance(value, str) else ""
    return {"chars": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


async def replay_track(server: Any, model: Any, *, recorded: bool) -> dict[str, Any]:
    """运行真实 track 链路，固定正文只在解析入口适配，保留框架错误映射。

    Args:
        server: 已初始化的业务 TrackServer 实例。
        model: 按原始记录重建的 TrackModel。
        recorded: 是否验证固定录制响应；该模式禁止业务网络调用。

    Returns:
        框架返回、解析原异常、入口源码身份及实际收到的正文身份。
    """
    server_type = type(server)
    original_do_track = server_type.do_track
    expected = fingerprint(model.response)
    calls: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    network: list[str] = []
    active = recorded

    def deny_network(event: str, arguments: tuple[Any, ...]) -> None:
        """仅在验证执行期间拒绝业务网络；不将目标或凭据写入报告。"""
        if active and event in {"socket.connect", "socket.sendto", "socket.getaddrinfo"}:
            network.append(event)
            raise OSError("RecordedReplayNetworkDenied: 固定响应验证禁止网络请求")

    if recorded:
        sys.addaudithook(deny_network)

    async def traced_do_track(instance: Any, track_model: Any, gather: Any) -> Any:
        """只在真实解析入口注入旧插件正文，不执行或改写运输商脚本入口。"""
        restored_children: list[tuple[Any, Any, Any]] = []
        instrumented = {id(gather)}
        original_process = gather.process_html
        original_read = gather.read_html

        def observe_child(child: Any) -> None:
            """共享分发器自然选择子解析器后才观测，不替业务猜渠道。"""
            if child is None or id(child) in instrumented:
                return
            instrumented.add(id(child))
            process = child.process_html
            read = child.read_html
            restored_children.append((child, process, read))

            async def child_process(current_model: Any) -> Any:
                return await traced_process(current_model, child, process)

            child.process_html = child_process
            if recorded:
                child.read_html = denied_read

        async def denied_read(*args: Any, **kwargs: Any) -> Any:
            """固定正文回放不应进入采集入口；在客户端调用前拒绝。"""
            network.append("gather.read_html")
            raise RuntimeError("RecordedReplayRequestDenied: 录制正文验证不得调用 read_html")

        async def traced_process(current_model: Any, current_gather: Any = gather, process: Any = original_process) -> Any:
            """记录入口和原异常后原样交还框架处理。"""
            legacy = any(base.__module__.startswith("gather_providers.legacy_spider_provider.")
                         for base in type(current_gather).__mro__)
            target = current_gather._get_spider(current_model) if legacy else current_gather
            if recorded and legacy:
                target.index_html = current_model.response
            actual = target.index_html if legacy else current_model.response
            function = inspect.unwrap(target.process_html if legacy else process)
            module = getattr(function, "__module__", "")
            # 共享分发器不是最终正文消费者；未观测到叶子入口时必须保持未验证。
            business_entry = legacy or module.startswith(("gathers.", "spiders."))
            source = inspect.getsourcefile(function) or ""
            source_path = Path(source) if source else None
            call = {
                "adapter": "legacy_index_html" if legacy else "track_model_response",
                "entrypoint": function.__qualname__,
                "source": source,
                "module": module,
                "business_entry": business_entry,
                "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest()
                if source_path and source_path.is_file() else "",
                "input": fingerprint(actual),
                "input_matches_recording": fingerprint(actual) == expected,
                "request_numbers": [item.number for item in current_model.requests],
            }
            previous_calls = len(calls)
            # 已知 Channel/Shadow 分发按其自然初始化过程观测子入口；未知分发不伪造证据。
            ensure = getattr(current_gather, "ensure_gather", None)
            def traced_ensure(model: Any) -> Any:
                value = ensure(model)
                observe_child(getattr(current_gather, "gather", None))
                return value
            if not business_entry:
                observe_child(getattr(current_gather, "gather", None))
                if callable(ensure):
                    current_gather.ensure_gather = traced_ensure
            if business_entry:
                calls.append(call)
            try:
                return await process(current_model)
            except Exception as error:
                if business_entry or len(calls) == previous_calls:
                    errors.append({
                        "type": type(error).__name__, "message": str(error),
                        "frames": [{"file": frame.filename, "line": frame.lineno, "function": frame.name}
                                   for frame in traceback.extract_tb(error.__traceback__)
                                   if frame.name not in {"traced_process", "child_process"}],
                        "request_numbers": call["request_numbers"],
                    })
                raise
            finally:
                if not business_entry:
                    if callable(ensure):
                        current_gather.ensure_gather = ensure
                    if len(calls) == previous_calls:
                        calls.append(call)

        gather.process_html = traced_process
        if recorded:
            gather.read_html = denied_read
        try:
            return await original_do_track(instance, track_model, gather)
        finally:
            for child, process, read in reversed(restored_children):
                child.process_html = process
                child.read_html = read
            gather.process_html = original_process
            gather.read_html = original_read

    server_type.do_track = traced_do_track
    try:
        response = await server.track(track_model=model)
    finally:
        active = False
        server_type.do_track = original_do_track
    result = response.model_dump(mode="json")
    result["replay_parse_errors"] = errors
    result["replay_observation"] = {
        "schema_version": 3,
        "expected_input": expected,
        "parser_calls": calls,
        "network_attempts": len(network),
        "input_delivered": bool(calls) and all(call["input_matches_recording"] and call["business_entry"] for call in calls),
    }
    return result
