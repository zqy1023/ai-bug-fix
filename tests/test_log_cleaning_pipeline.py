"""日志标准化与清洗回归：异常溯源格式、结构化字段、归属初判、传输层出池。"""

import json
import unittest

from infrastructure.es.logs import ESLogAdapter
from workflow.stages.log_pipeline.classify import classify_blame, response_kind
from workflow.stages.log_pipeline.deduplicate_error.node import consume, write_groups
from workflow.stages.log_pipeline.discover_logs.normalizer import error_field, error_parts


def _record(**overrides):
    """构造一条满足清洗契约的规范化记录。"""
    base = {
        "service_name": "gather.py.track",
        "biztype": "framework.track_server",
        "carrier": "TpcIndia",
        "req": '{"carrier":"TpcIndia"}',
        "raw_error": "builtins.IndexError, list index out of range",
        "normalized_error": "builtins.IndexError, list index out of range",
        "source_index": 0,
        "message": "log line",
        "message_format": "req",
        "exception_class": "builtins.IndexError",
        "exception_message": "list index out of range",
        "frame": "tpcindia.py:41:read_html",
        "response_kind": "empty",
        "blame": "code",
    }
    base.update(overrides)
    return base


class TraceFormatTests(unittest.TestCase):
    """第四种格式：无 req/response，但溯源路径能还原 carrier。"""

    def test_trace_format_recovers_carrier(self):
        line = (
            "2026-10-29 10:42:06,990 [172.17.210.177-10] ERROR gather.py.track - "
            "yq_common.time_utils.time_utils:未知的时区: ICT, "
            "异常溯源:gathers.GermanyGlobalMailUS.main_batch"
        )
        parsed = ESLogAdapter.parse_message(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["carrier"], "GermanyGlobalMailUS")
        self.assertEqual(parsed["message_format"], "trace")
        self.assertEqual(parsed["error"], "yq_common.time_utils.time_utils:未知的时区: ICT")
        request = json.loads(parsed["req"])
        self.assertEqual(request["source"], "gathers.GermanyGlobalMailUS.main_batch")

    def test_trace_format_supports_spiders(self):
        line = "ERROR x - boom, 异常溯源:spiders.Dachser.dachser"
        parsed = ESLogAdapter.parse_message(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["carrier"], "Dachser")

    def test_trace_format_without_known_root_is_rejected(self):
        line = "ERROR x - boom, 异常溯源:unknown.Dachser.main"
        self.assertIsNone(ESLogAdapter.parse_message(line))


class ErrorPartsTests(unittest.TestCase):
    def test_splits_class_message_and_frame(self):
        klass, message, frame = error_parts(
            "builtins.IndexError, list index out of range "
            "file: tpcindia.py, line: 41, function: read_html"
        )
        self.assertEqual(klass, "builtins.IndexError")
        self.assertEqual(message, "list index out of range")
        self.assertEqual(frame, "tpcindia.py:41:read_html")

    def test_business_topic_is_not_treated_as_exception_class(self):
        """`framework.track_server` 这类业务主题不能算异常类。"""
        klass, _, _ = error_parts("framework.track_server:解析异常 | msg: 没有单号")
        self.assertEqual(klass, "")

    def test_request_response_prefix_is_not_exception_class(self):
        """`requestResponse` 前缀不是类名；error_type 里的 ProcessError 才是。"""
        klass, _, _ = error_parts("requestResponse: code=404, error_type=ProcessError")
        self.assertNotIn("requestResponse", klass)
        self.assertEqual(klass, "ProcessError")


class ErrorFieldTests(unittest.TestCase):
    def test_extracts_business_field_path(self):
        """时间格式错误没有栈帧，但 key: 给出了字段路径。"""
        text = (
            '时间格式错误:未识别的时间格式, model:{"datetime":null}, '
            "key:track_result_events.datetime, carrier:Usa"
        )
        self.assertEqual(error_field(text), "track_result_events.datetime")

    def test_missing_key_returns_empty(self):
        self.assertEqual(error_field("builtins.IndexError, boom"), "")


class BlameTests(unittest.TestCase):
    def test_process_error_business_result_is_code(self):
        """code=404 但 error_type=ProcessError 是处理失败，不是无单。"""
        self.assertEqual(
            classify_blame(
                message_format="requestResponse",
                exception_class="ProcessError", frame="", kind="empty",
                error=(
                    "requestResponse: code=404, error_type=ProcessError, "
                    "message=unconverted data remains: Z"
                ),
            ),
            "code",
        )

    def test_plain_business_code_without_processing_type_stays_business(self):
        self.assertEqual(
            classify_blame(
                message_format="requestResponse",
                exception_class="", frame="", kind="empty",
                error="requestResponse: code=404, error_type=, message=no data",
            ),
            "business",
        )

    def test_network_exception_is_transport(self):
        self.assertEqual(
            classify_blame(
                message_format="req",
                exception_class="requests.exceptions.ProxyError",
                frame="", kind="text", error="proxy boom",
            ),
            "transport",
        )

    def test_chunked_encoding_error_is_transport(self):
        """传输层异常不止 Proxy/SSL：分块编码中断同样改业务代码修不了。"""
        self.assertEqual(
            classify_blame(
                message_format="req",
                exception_class="requests.exceptions.ChunkedEncodingError",
                frame="", kind="text", error="connection broken",
            ),
            "transport",
        )

    def test_local_frame_is_code(self):
        self.assertEqual(
            classify_blame(
                message_format="req",
                exception_class="builtins.IndexError",
                frame="tpcindia.py:41:read_html", kind="json", error="boom",
            ),
            "code",
        )

    def test_business_404_is_business(self):
        self.assertEqual(
            classify_blame(
                message_format="requestResponse",
                exception_class="", frame="", kind="empty",
                error="requestResponse: code=404, error_type=NoDataError, message=x",
            ),
            "business",
        )

    def test_json_decode_against_html_is_upstream(self):
        self.assertEqual(
            classify_blame(
                message_format="req",
                exception_class="json.decoder.JSONDecodeError",
                frame="", kind="html", error="Expecting value",
            ),
            "upstream",
        )

    def test_json_decode_against_json_is_code(self):
        self.assertEqual(
            classify_blame(
                message_format="req",
                exception_class="json.decoder.JSONDecodeError",
                frame="", kind="json", error="Expecting value",
            ),
            "code",
        )

    def test_no_evidence_is_undetermined_not_unknown_carrier(self):
        """undetermined 表示"判不出归属"，与 carrier 无关。"""
        self.assertEqual(
            classify_blame(
                message_format="model", exception_class="", frame="",
                kind="empty", error="framework.track_server:解析异常",
            ),
            "undetermined",
        )

    def test_response_kind(self):
        self.assertEqual(response_kind(""), "empty")
        self.assertEqual(response_kind('{"a": 1}'), "json")
        self.assertEqual(response_kind("<html></html>"), "html")
        self.assertEqual(response_kind("<?xml version='1.0'?><a/>"), "xml")
        self.assertEqual(response_kind("plain"), "text")


class CleanFilterTests(unittest.TestCase):
    def test_transport_record_never_enters_repair_pool(self):
        groups = {}
        outcome, reason = consume(groups, _record(
            exception_class="requests.exceptions.ProxyError",
            blame="transport",
            raw_error="requests.exceptions.ProxyError, HTTPSConnectionPool",
            normalized_error="requests.exceptions.ProxyError, HTTPSConnectionPool",
        ), ())
        self.assertEqual(outcome, "unrepairable")
        self.assertEqual(reason, "transport:ProxyError")
        self.assertEqual(groups, {})

    def test_code_record_keeps_structured_fields(self):
        groups = {}
        outcome, _ = consume(groups, _record(), ())
        self.assertEqual(outcome, "new")
        problem = next(iter(groups.values()))["problem"]
        self.assertEqual(problem["exception_class"], "builtins.IndexError")
        self.assertEqual(problem["frame"], "tpcindia.py:41:read_html")
        self.assertEqual(problem["blame"], "code")
        self.assertEqual(problem["message_format"], "req")

    def test_ignore_rule_still_applies_before_classification(self):
        groups = {}
        outcome, reason = consume(groups, _record(
            normalized_error="redis.exceptions.ConnectionError, boom",
            blame="transport",
        ), ("redis.exceptions.ConnectionError",))
        self.assertEqual(outcome, "ignored")
        self.assertEqual(reason, "ignore_rule:redis.exceptions.ConnectionError")

    def test_write_groups_reports_quality(self):
        records = [
            _record(),
            _record(source_index=1, message_format="model",
                    exception_class="requests.exceptions.ProxyError", blame="transport",
                    normalized_error="requests.exceptions.ProxyError, boom",
                    raw_error="requests.exceptions.ProxyError, boom"),
            _record(source_index=2, message_format="trace",
                    exception_class="", blame="undetermined", response_kind="empty",
                    normalized_error="unknown tz, boom", raw_error="unknown tz, boom",
                    req='{"carrier":"TpcIndia","source":"gathers.TpcIndia.main"}'),
        ]
        result = write_groups(iter(records), ())
        self.assertEqual(result["input_count"], 3)
        self.assertEqual(result["ignored_count"], 1)
        self.assertEqual(len(result["records"]), 2)
        quality = result["quality"]
        self.assertEqual(quality["message_formats"], {"req": 1, "model": 1, "trace": 1})
        self.assertEqual(quality["ignored_reasons"], {"transport:ProxyError": 1})
        self.assertEqual(quality["response_present"], 0)
        self.assertEqual(quality["exception_classes"]["builtins.IndexError"], 1)


class PlaceholderCarrierTests(unittest.TestCase):
    """占位 carrier 不能当成运输商：无法定位可写目录，必须在准入阶段拒绝。"""

    def test_inline_placeholder_carrier_is_rejected(self):
        self.assertIsNone(ESLogAdapter.parse_message(
            "ERROR x - common.base_func:boom, carrier:unknown, num:1"
        ))

    def test_model_placeholder_carrier_is_rejected(self):
        line = 'ERROR x - t:解析异常,model:{"carrier":"N/A","requests":[]}'
        self.assertIsNone(ESLogAdapter.parse_message(line))


if __name__ == "__main__":
    unittest.main()
