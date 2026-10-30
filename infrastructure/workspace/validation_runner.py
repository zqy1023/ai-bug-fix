"""平台固定验证入口：录制响应回放与原始真实请求分别执行。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import traceback

from request_input import original_request
from runtime_environment import environment_receipt, prepare_runtime_logging, initialize_framework_runtime, runtime_configuration_receipt


def main() -> None:
    prepare_runtime_logging()
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    request = original_request(payload["problem"])
    if payload["kind"] == "replay":
        request["response"] = payload["problem"]["response"]
    receipt = {"passed": False, "reproduced": False, "assertions": [], "environment": environment_receipt()}
    try:
        sys.path.insert(0, str(Path.cwd()))
        initialize_framework_runtime()
        from framework.models import TrackModel
        from framework.track_server import TrackServer
        response = asyncio.run(TrackServer().track(TrackModel.model_validate(request)))
        receipt["response"] = response.model_dump(mode="json")
        errors = [getattr(item.error_type, "name", "") for item in response.items]
        receipt["reproduced"] = "ProcessError" in errors
        # 默认验证器只承认真实成功。服务异常分类需要自定义业务断言，不能冒充恢复。
        checks = [
            {"name": "返回项数与原始请求一致", "passed": len(response.items) == len(request["requests"])},
            {"name": "返回单号与原始请求一致", "passed": sorted(item.request_item.number for item in response.items)
             == sorted(item["number"] for item in request["requests"])},
            {"name": "所有请求均有成功轨迹结果", "passed": bool(response.items) and all(
                getattr(item.error_type, "name", "") == "Succeed" and bool(item.track_result)
                and bool(getattr(item.track_result, "track_result_events", None))
                for item in response.items)},
        ]
        receipt.update(assertions=checks, passed=all(item["passed"] for item in checks), error_types=errors)
        receipt["reason"] = "业务断言通过" if receipt["passed"] else "原始请求未返回全部有效轨迹，业务断言未通过"
        if payload["phase"] == "baseline":
            receipt["reason"] = "原始响应复现解析异常" if receipt["reproduced"] else "原始响应未复现解析异常"
    except Exception as error:
        receipt.update(reason=f"验证环境或执行失败: {type(error).__name__}: {error}", execution_error=traceback.format_exc())
    receipt['runtime_configuration'] = runtime_configuration_receipt()
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
