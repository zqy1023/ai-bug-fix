"""日志清洗 → 去重 → 派单 的端到端流程测试（不触网、不调模型）。

覆盖：五种消息格式入库、传输层出池并留原因、频次决定每组代表、结构化字段
一路透传到任务载荷。
"""

import json
import unittest

from infrastructure.es.logs import ESLogAdapter
from workflow.stages.log_pipeline.deduplicate_error.node import write_groups
from workflow.stages.log_pipeline.identify_carrier.node import (
    select_representative,
    task_problem,
)
from workflow.stages.log_pipeline.normalize_error.node import _normalize


def _req_line(carrier: str, number: str, error: str, biztype: str = "framework.track_server") -> str:
    return (
        f'2026-10-29 10:00:00,000 [1] ERROR gather.py.track - {biztype}:'
        f'采集执行异常 req:{{"carrier":"{carrier}","requests":[{{"number":"{number}"}}]}} '
        f"error: {error}"
    )


def _model_line(carrier: str, number: str, response: str) -> str:
    payload = {
        "track_config": {"timeout": 30000},
        "carrier": carrier,
        "requests": [{"number": number}],
        "response": response,
    }
    return (
        "2026-10-29 10:00:00,000 [1] ERROR gather.py.track - framework.track_server:"
        "解析异常,model:" + json.dumps(payload, ensure_ascii=False) + ", msg:None"
    )


def _run_pipeline(messages: list[str]):
    """按真实节点顺序执行：准入 → 规范化 → 去重 → 分组派单。"""
    normalized = []
    parse_failed = 0
    for index, message in enumerate(messages):
        row = ESLogAdapter.parse_hit({"_source": {
            "service_name": "gather.py.track",
            "biztype": _biztype_of(message),
            "message": message,
        }})
        if row is None:
            parse_failed += 1
            continue
        normalized.append(_normalize(row, index))
    result = write_groups(iter(normalized), ())
    return result, parse_failed


def _biztype_of(message: str) -> str:
    """从 message 里推出 biztype，模拟 ES 顶层字段。"""
    for marker in ("yq_common.time_utils.time_utils:", "common.base_func:"):
        if marker in message:
            return marker.rstrip(":")
    return "framework.track_server"


def _tasks(records):
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        groups.setdefault((record["biztype"], record["carrier"]), []).append(record)
    return {key: task_problem(select_representative(items)) for key, items in groups.items()}


class LogFlowTests(unittest.TestCase):
    def setUp(self):
        self.messages = [
            # 同一签名 ×3：应当以 3 次成为组内代表
            _req_line("TEST", "A1", "builtins.IndexError, list index out of range file: main.py, line: 41, function: read_html"),
            _req_line("TEST", "A2", "builtins.IndexError, list index out of range file: main.py, line: 41, function: read_html"),
            _req_line("TEST", "A3", "builtins.IndexError, list index out of range file: main.py, line: 41, function: read_html"),
            # 同组长尾 ×1
            _req_line("TEST", "B1", "builtins.KeyError, 'datetime' file: main.py, line: 88, function: process_html"),
            # 解析异常：带原始响应（命中拦截页标记 → 上游责任）
            _model_line("TEST2", "C1", "<html><body>Access Denied</body></html>"),
            # 业务结果 404
            (
                "requestResponse:{'code': 404, 'data': [{'number': 'D1', 'carrier': 'TEST3'}], "
                "'message': 'unconverted data remains: Z', 'error_type': 'ProcessError'}"
            ),
            # 异常溯源格式
            "2026-10-29 10:00:00,000 [1] ERROR gather.py.track - "
            "yq_common.time_utils.time_utils:未知的时区: ICT, 异常溯源:gathers.TEST4.main_batch",
            # 传输层 ×5：必须出池
            _req_line("TEST5", "E1", "requests.exceptions.ProxyError, HTTPSConnectionPool boom"),
            _req_line("TEST5", "E2", "requests.exceptions.ProxyError, HTTPSConnectionPool boom"),
            _req_line("TEST5", "E3", "requests.exceptions.ProxyError, HTTPSConnectionPool boom"),
            _req_line("TEST5", "E4", "requests.exceptions.ProxyError, HTTPSConnectionPool boom"),
            _req_line("TEST5", "E5", "requests.exceptions.ProxyError, HTTPSConnectionPool boom"),
            # 占位 carrier：无法定位可写目录，准入拒绝
            "ERROR x - common.base_func:boom, carrier:unknown, num:F1",
        ]

    def test_flow_end_to_end(self):
        result, parse_failed = _run_pipeline(self.messages)
        quality = result["quality"]

        self.assertEqual(parse_failed, 1, "占位 carrier 必须被准入拒绝")
        self.assertEqual(result["input_count"], 12)

        # 传输层 5 条出池，且原因可追溯
        self.assertEqual(result["ignored_count"], 5)
        self.assertEqual(quality["ignored_reasons"], {"transport:ProxyError": 5})

        # 格式与类名分布反映"摄入构成"（含随后被出池的记录），出池原因单列在 ignored_reasons
        self.assertEqual(
            quality["message_formats"],
            {"req": 9, "model": 1, "requestResponse": 1, "trace": 1},
        )

        tasks = _tasks(result["records"])
        self.assertNotIn(
            ("framework.track_server", "TEST5"), tasks,
            "只有传输层异常的组不应产生任何任务",
        )

        # 频次决定代表：3 次的 IndexError 胜过 1 次的 KeyError
        representative = tasks[("framework.track_server", "TEST")]
        self.assertEqual(representative["exception_class"], "builtins.IndexError")
        self.assertEqual(representative["blame"], "code")
        self.assertEqual(representative["frame"], "main.py:41:read_html")

        # 结构化字段一路透传到任务载荷
        upstream = tasks[("framework.track_server", "TEST2")]
        self.assertEqual(upstream["message_format"], "model")
        self.assertEqual(upstream["response_kind"], "html")
        self.assertEqual(upstream["response"], "<html><body>Access Denied</body></html>")
        self.assertEqual(upstream["blame"], "upstream")

        # code=404 但 error_type=ProcessError 是处理失败（日期串带 Z），不是无单
        business = tasks[("framework.track_server", "TEST3")]
        self.assertEqual(business["blame"], "code")
        self.assertEqual(business["message_format"], "requestResponse")

        trace = tasks[("yq_common.time_utils.time_utils", "TEST4")]
        self.assertEqual(trace["message_format"], "trace")
        self.assertEqual(trace["blame"], "undetermined")


if __name__ == "__main__":
    unittest.main()
