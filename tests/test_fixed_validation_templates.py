"""固定模板只能接受任务数据，不能把日志内容变成脚本指令。"""
import ast
from pathlib import Path

from app.agents.review.tools.run_regression.request_runner import make_request_script
from app.agents.review.tools.verify_issue.replay_runner import _REPLAY_SCRIPT


def test_recorded_replay_template_parses_and_uses_data_file():
    tree = ast.parse(_REPLAY_SCRIPT)
    assert tree.body
    assert 'Path(sys.argv[1]).read_text' in _REPLAY_SCRIPT
    assert 'recorded=bool(payload["response"])' in _REPLAY_SCRIPT


def test_request_template_preserves_identity_and_treats_payload_as_data():
    number = "sample'); raise RuntimeError('injected') #"
    script, inputs = make_request_script("Test", {
        "req": {"carrier": "Test", "requests": [
            {"number": number, "gather_channel": "Original", "param": "original-value"},
        ]},
        "track_config": {"timeout": 17000},
    })
    tree = ast.parse(script)
    assert inputs["requests"][0]["number"] == number
    assert inputs["requests"][0]["gather_channel"] == "Original"
    assert inputs["track_config"]["timeout"] == 17000
    assert not any(isinstance(node, ast.Raise) for node in ast.walk(tree))
    assert 'from server_dev import main' in script
    assert 'res = main(model)' in script
    assert 'observe_business_errors' in script
    assert Path('app/agents/review/tools/run_regression/request_script.py.tmpl').is_file()
