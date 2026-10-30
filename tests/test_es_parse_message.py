"""ES 异常日志 message 解析回归。

线上日志存在三种不同格式：``req:``、``model:{...}``（解析异常，带原始响应）、
``requestResponse:{...}``（业务结果，Python repr）。后两种过去因缺少 ``req:`` 标记
被整条丢弃，导致归一化产物里的 ``response`` 永远为空、无法重放验证归因。
"""

import json
import unittest

from infrastructure.es.logs import ESLogAdapter


def _model_line(payload: dict, topic: str = "framework.track_server:解析异常", tail: str = ", msg:None") -> str:
    """构造真实格式的解析异常日志行。"""
    return (
        "2026-10-29 09:49:41,992 [172.17.211.26-80844] ERROR gather.py.track - "
        f"{topic},model:" + json.dumps(payload, ensure_ascii=False) + tail
    )


class ParseMessageFormatTests(unittest.TestCase):
    def test_standard_req_format_still_parsed(self):
        line = (
            '2026-10-29 09:58:07,166 [1] ERROR gather.py.track - framework.track_server:'
            '采集执行异常 req:{"carrier":"TpcIndia","requests":[{"number":"A1"}]} '
            "error: builtins.IndexError, list index out of range file: tpcindia.py, line: 41"
        )
        parsed = ESLogAdapter.parse_message(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["carrier"], "TpcIndia")
        self.assertEqual(json.loads(parsed["req"])["requests"], [{"number": "A1"}])
        self.assertIn("IndexError", parsed["error"])

    def test_req_format_keeps_response_and_track_config(self):
        line = (
            'ERROR x - topic req:{"carrier":"X"} '
            'track_config:{"timeout":30000} response:{"a":1} error: boom'
        )
        parsed = ESLogAdapter.parse_message(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(json.loads(parsed["track_config"])["timeout"], 30000)
        self.assertEqual(json.loads(parsed["response"]), {"a": 1})
        self.assertEqual(parsed["error"], "boom")

    def test_parse_exception_model_format_carries_response(self):
        """核心回归：带 response 的解析异常必须被收下，而不是丢弃。"""
        response = "<b>ECSHOP info</b>: Can't Connect MySQL Server(127.0.0.1:3306)!\n\n<br /><br />"
        payload = {
            "track_config": {"timeout": 30000},
            "carrier": "Shangtong",
            "response": response,
            "requests": [{"number": "261014KM61XBA2", "gather_channel": "Default"}],
        }
        parsed = ESLogAdapter.parse_message(_model_line(payload))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["carrier"], "Shangtong")
        self.assertEqual(parsed["response"], response)
        self.assertEqual(json.loads(parsed["track_config"]), {"timeout": 30000})
        request = json.loads(parsed["req"])
        self.assertEqual(request["carrier"], "Shangtong")
        self.assertEqual(request["requests"][0]["number"], "261014KM61XBA2")
        self.assertEqual(parsed["error"], "framework.track_server:解析异常")

    def test_model_format_appends_meaningful_msg(self):
        payload = {"carrier": "Unitrade", "requests": [], "response": "<html/>"}
        parsed = ESLogAdapter.parse_message(
            _model_line(payload, tail=", msg:Expecting value: line 1 column 1")
        )
        self.assertIsNotNone(parsed)
        self.assertIn("Expecting value", parsed["error"])
        self.assertIn("解析异常", parsed["error"])

    def test_msg_inside_response_is_not_treated_as_tail(self):
        """响应正文里出现 msg: 时不能误当成日志尾部字段。"""
        payload = {"carrier": "X", "requests": [], "response": "upstream says msg:hacked"}
        parsed = ESLogAdapter.parse_message(_model_line(payload, tail=""))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["error"], "framework.track_server:解析异常")

    def test_model_format_without_carrier_is_rejected(self):
        parsed = ESLogAdapter.parse_message(
            _model_line({"requests": [], "response": "x"})
        )
        self.assertIsNone(parsed)

    def test_model_format_with_broken_json_is_rejected(self):
        line = "ERROR x - t:解析异常,model:{not json, msg:None"
        self.assertIsNone(ESLogAdapter.parse_message(line))

    def test_business_result_repr_parsed(self):
        line = (
            "requestResponse:{'code': 404, 'data': [{'number': 'TBA335959108801', "
            "'carrier': 'AmazonShippingMCF'}], 'message': 'unconverted data remains: Z', "
            "'error_type': 'ProcessError'}"
        )
        parsed = ESLogAdapter.parse_message(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["carrier"], "AmazonShippingMCF")
        self.assertEqual(json.loads(parsed["req"])["requests"][0]["number"], "TBA335959108801")
        # 本地解析缺陷的证据必须保留，不能简化成"无单"。
        self.assertIn("unconverted data remains: Z", parsed["error"])
        self.assertIn("ProcessError", parsed["error"])
        self.assertIn("404", parsed["error"])

    def test_business_result_tolerates_trailing_text(self):
        line = (
            "requestResponse:{'code': 500, 'data': [{'number': 'N1', 'carrier': 'DHL'}], "
            "'message': 'oops', 'error_type': 'ServiceError'} trailing"
        )
        parsed = ESLogAdapter.parse_message(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["carrier"], "DHL")

    def test_inline_carrier_format(self):
        """旧框架桥接层的「时间解析错误」：carrier 与单号写在正文里。"""
        line = (
            "2026-10-29 10:27:40,701 [172.17.211.69-8] ERROR gather.py.track - "
            "gather_providers.legacy_spider_provider.legacy_spider_provider:时间解析错误, "
            "carrier:Mothership, num:MS9811882"
        )
        parsed = ESLogAdapter.parse_message(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["carrier"], "Mothership")
        self.assertEqual(parsed["message_format"], "inline")
        self.assertEqual(
            parsed["error"],
            "gather_providers.legacy_spider_provider.legacy_spider_provider:时间解析错误",
        )
        request = json.loads(parsed["req"])
        self.assertEqual(request["requests"][0]["number"], "MS9811882")

    def test_inline_format_keeps_body_as_response(self):
        line = (
            "ERROR x - common.datadome_base_utils:签发verify_url失败, "
            "carrier:Mothership, body=<html>blocked</html>"
        )
        parsed = ESLogAdapter.parse_message(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["response"], "<html>blocked</html>")

    def test_inline_format_supports_capitalized_and_number_field(self):
        """线上存在 `Carrier:` 大写与 `number:` 字段的变体。"""
        line = (
            "ERROR x - common.base_func:Carrier:TWSExpress,number:2UW0S0W018161,"
            'msg:{"status":"error"}'
        )
        parsed = ESLogAdapter.parse_message(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["carrier"], "TWSExpress")
        self.assertEqual(json.loads(parsed["req"])["requests"][0]["number"], "2UW0S0W018161")
        self.assertEqual(parsed["response"], '{"status":"error"}')

    def test_inline_parser_does_not_override_known_formats(self):
        line = 'ERROR x - topic req:{"carrier":"Real"} carrier:Decoy'
        parsed = ESLogAdapter.parse_message(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["carrier"], "Real")
        self.assertEqual(parsed["message_format"], "req")

    def test_trace_root_outside_known_roots_is_rejected(self):
        """溯源根是 gather_providers 时无法还原 carrier，必须拒绝而不是瞎猜。"""
        line = (
            "ERROR x - yq_common.time_utils:未知的时区: --4:00, "
            "异常溯源:gather_providers.legacy_spider_provider.legacy_spider_provider"
        )
        self.assertIsNone(ESLogAdapter.parse_message(line))

    def test_unrecognized_message_returns_none(self):
        self.assertIsNone(ESLogAdapter.parse_message("2026-10-29 ERROR something happened"))


class ParseHitTests(unittest.TestCase):
    def test_parse_hit_exposes_model_format_fields(self):
        payload = {
            "carrier": "Shangtong",
            "requests": [{"number": "261014KM61XBA2", "gather_channel": "Default"}],
            "response": "<b>ECSHOP info</b>: Can't Connect MySQL Server",
        }
        hit = {
            "_source": {
                "service_name": "gather.py.track",
                "biztype": "framework.track_server",
                "message": _model_line(payload),
            }
        }
        row = ESLogAdapter.parse_hit(hit)
        self.assertIsNotNone(row)
        self.assertEqual(row["service_name"], "gather.py.track")
        self.assertEqual(row["biztype"], "framework.track_server")
        self.assertEqual(row["carrier"], "Shangtong")
        self.assertEqual(row["response"], payload["response"])


if __name__ == "__main__":
    unittest.main()
