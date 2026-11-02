"""交付文档必须带完整原始异常（2026-10-31 端到端发现）。

真实缺陷：ApexMotor 那轮的根因是响应正文里的 ``PRO=`` 前缀，但交付分支上的
``task_summary.md`` 只写了"上游响应：未捕获"，``response`` / ``req`` 两个字段
在 ``identify_carrier`` 的 ``RecordView.trim`` 里被裁掉了，复核者拿到交付文档
无法判断修复对不对。

本文件锁住两件事：

1. ``identify_carrier`` 的步骤记录保留 ``req`` / ``response`` /
   ``original_exception_message`` / ``related_messages``。
2. ``task_summary.md`` 渲染完整响应正文与请求参数，而不是只报字符数。
"""

import json

from infrastructure.git.task_summary import render_task_summary


def _problem() -> dict:
    return {
        "service_name": "gather.py.track",
        "biztype": "framework.track_server",
        "carrier": "ApexMotor",
        "message": "2026-10-31 13:29:21,863 ERROR gather.py.track - framework.track_server:解析异常 "
                   'model:{"carrier":"ApexMotor","response":"<br>PRO=01259767394…(已截断，原长 782 字符)',
        "error": "framework.track_server:解析异常 | msg: 未找到BOL",
        "raw_error": "framework.track_server:解析异常 | msg: 未找到BOL",
        "req": '{"carrier": "ApexMotor", "requests": [{"number": "01259767394"}]}',
        "response": "<br>PRO=01259767394<br><span data-i18n='pages.quicktrack.heading1'></span> 1259767394<br>Delivered",
        "original_exception_message": "2026-10-31 13:29:21,863 ERROR gather.py.track - 完整归档异常正文",
        "related_messages": ["2026-10-31 13:29:22,001 ERROR gather.py.track - 关联日志正文"],
    }


def test_task_summary_contains_full_response_body():
    report = {
        "carrier": "ApexMotor",
        "biztype": "framework.track_server",
        "result": "CHANGED_DELIVERED",
        "terminal_reason": "已在 process_html 内扩展数据块前缀识别。",
        "root_cause": "只匹配 BOL=，实际响应使用 PRO=。",
        "changed_files": ["gathers/ApexMotor/main.py"],
        "problem": _problem(),
        "elapsed_ms": 1234,
        "tokens": 51242,
    }
    text = render_task_summary(report)

    # 完整响应正文必须在文档里，而不只是字符数。
    assert "PRO=01259767394" in text, "task_summary 缺少完整响应正文"
    assert "Delivered" in text
    assert "上游响应原文" in text
    # 请求参数全文。
    assert "01259767394" in text
    assert "原始请求参数" in text
    # 归档恢复的完整异常与关联日志。
    assert "归档恢复的完整异常" in text
    assert "关联日志 1" in text


def test_task_summary_says_not_captured_when_response_absent():
    report = {
        "carrier": "X",
        "biztype": "b",
        "result": "SKIPPED",
        "terminal_reason": "上游故障",
        "problem": {k: v for k, v in _problem().items() if k not in {"response", "req"}},
    }
    text = render_task_summary(report)
    assert "上游响应：未捕获" in text


def test_identify_carrier_record_keeps_raw_request_and_response():
    """步骤记录不能把 req/response 裁掉——终态报告就是从这里读的。"""
    from workflow.stages.log_pipeline.identify_carrier import SPEC

    assert SPEC.output_record is not None
    trimmed = None
    for dotted, keys in SPEC.output_record.trim:
        if dotted == "tasks.problem":
            trimmed = set(keys)
    assert trimmed is not None, "identify_carrier 必须声明 tasks.problem 的保留字段"
    for field in ("req", "response", "original_exception_message", "related_messages"):
        assert field in trimmed, f"identify_carrier 步骤记录丢弃了 {field}"


def test_delivered_report_keeps_problem_payload():
    """finalize 报告的 problem 必须带上完整 req/response。"""
    import inspect

    from workflow.stages.finalize.node import _problem

    source = inspect.getsource(_problem)
    for field in ("req", "response", "original_exception_message", "related_messages"):
        assert field in source, f"finalize 报告的 problem 没有保留 {field}"
