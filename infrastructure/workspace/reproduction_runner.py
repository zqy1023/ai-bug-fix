"""在独立子进程中调用当前采集入口，捕获被插件吞掉的业务栈。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import traceback

from runtime_environment import environment_receipt, prepare_runtime_logging, initialize_framework_runtime, runtime_configuration_receipt


def main() -> None:
    prepare_runtime_logging()
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    project = Path.cwd().resolve()
    carrier = payload["request"]["carrier"]
    roots = [(project / name / carrier).resolve() for name in ("gathers", "spiders")]
    captured: list[dict[str, str]] = []

    def trace(frame, event, arg):
        if event == "exception":
            source = Path(frame.f_code.co_filename).resolve()
            if any(source.is_relative_to(root) for root in roots):
                kind, error, tb = arg
                if not isinstance(error, (StopIteration, StopAsyncIteration, GeneratorExit, asyncio.CancelledError)):
                    captured.append({"exception_class": kind.__name__, "message": str(error),
                                     "traceback": "".join(traceback.format_exception(kind, error, tb))})
                    del captured[:-20]
        return trace

    receipt = {"reproduced": False, "request": payload["request"], "exceptions": [], "environment": environment_receipt()}
    try:
        sys.path.insert(0, str(project))
        initialize_framework_runtime()
        from framework.models import TrackModel
        from framework.track_server import TrackServer

        model = TrackModel.model_validate(payload["request"])
        server = TrackServer()
        sys.settrace(trace)
        try:
            response = asyncio.run(server.track(model))
        finally:
            sys.settrace(None)
        receipt["response"] = response.model_dump(mode="json")
        errors = [getattr(item.error_type, "name", "") for item in response.items]
        # 业务栈与最终解析失败必须同时存在，不能把内部已处理的异常算作复现。
        receipt["reproduced"] = bool(captured and "ProcessError" in errors)
        receipt["exceptions"] = captured if receipt["reproduced"] else []
        receipt["reason"] = "请求复现解析异常" if receipt["reproduced"] else "请求未复现带业务堆栈的解析异常"
    except Exception as error:
        receipt.update(reason=f"请求复现无法完成: {type(error).__name__}",
                       execution_error=traceback.format_exc())
    finally:
        sys.settrace(None)
    receipt['runtime_configuration'] = runtime_configuration_receipt()
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
