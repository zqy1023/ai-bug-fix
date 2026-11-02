"""搜索预算必须按搜索词计重，不受正则变体影响（2026-11-01 端到端发现）。

真实缺陷：DDUExpress 那轮 41 次工具调用里 **32 次是 Grep**（同一个词"解析异常"
搜了两次，其余是各种正则变体），把探索预算耗光后 SDK 轮数耗尽，
模型没输出任何结构化结论，任务判 FAILED。

原有的"相同工具参数已读取两次即拒"只拦**完全相同**的参数——模型换个正则
就绕过去了。这里按搜索词累计次数上限，逼模型转向"定位到文件后读一次"。
"""

from infrastructure.llm.tool_budget import ToolBudget


def test_same_search_word_is_stopped_after_repeat():
    """同一搜索词换不同参数（路径/上下文）也算重复检索。"""
    budget = ToolBudget(limit=64, writable=True)
    for index in range(3):
        # 参数各不相同（只有搜索词相同）——这正是模型绕开"同参"限制的方式。
        values = {"pattern": "解析异常", "path": f"gathers/TEST/dir{index}"}
        decision, _ = budget.decide("Grep", values, "allow", "")
        assert decision == "allow", f"第 {index + 1} 次应放行"

    decision, reason = budget.decide("Grep", {"pattern": "解析异常", "path": "gathers/TEST/dir3"}, "allow", "")
    assert decision == "deny", "同一搜索词超过上限后必须拒绝"
    assert "检索" in reason


def test_different_search_words_are_not_blocked():
    """不同搜索词各自独立计数，不能被一个词的次数连累。"""
    budget = ToolBudget(limit=64, writable=True)
    for index in range(4):
        decision, _ = budget.decide("Grep", {"pattern": f"关键词{index}"}, "allow", "")
        assert decision == "allow", f"不同搜索词第 {index + 1} 次应放行"


def test_regex_variant_of_same_word_still_counts():
    """换个正则写同一个词，仍然算同一个搜索词（上限内放行，超出即拒）。"""
    budget = ToolBudget(limit=64, writable=True)
    for index in range(3):
        decision, _ = budget.decide("Grep", {"pattern": "解析异常", "path": f"a{index}"}, "allow", "")
        assert decision == "allow", f"第 {index + 1} 次应放行（上限 3）"
    # 第 4 次：即使换了正则写法，也算同一个搜索词 → 拒绝
    decision, reason = budget.decide("Grep", {"pattern": "解析.*异常", "path": "c"}, "allow", "")
    assert decision == "deny", "同一搜索词的正则变体应计入同一计数"
    assert "检索" in reason


def test_read_is_not_affected_by_search_limit():
    """搜索上限只作用于 Grep/Glob，Read 不受影响。"""
    budget = ToolBudget(limit=64, writable=True)
    for index in range(10):
        decision, _ = budget.decide("Read", {"file_path": f"gathers/TEST/main{index}.py"}, "allow", "")
        assert decision == "allow"


def test_search_limit_does_not_break_write_budget():
    """搜索被限不影响写入与验证读取。"""
    budget = ToolBudget(limit=64, writable=True)
    for index in range(5):
        budget.decide("Grep", {"pattern": "同一个词", "path": f"d{index}"}, "allow", "")
    decision, _ = budget.decide("Edit", {"file_path": "gathers/TEST/main.py", "old_string": "a", "new_string": "b"}, "allow", "")
    assert decision == "allow", "写入不应被搜索限制挡住"


def test_write_still_allowed_after_exploration_stopped():
    """探索停止后写入必须继续放行。

    真实缺陷：连续拒绝 3 次会置 stop_kind=tool_access_blocked，之后**所有**工具
    （含写入）都被拒。模型可能已定位到文件正准备改，却因之前的无效 Grep 被
    连带拒绝，本可完成的修复变成"工具被拒、无结论"。
    """
    budget = ToolBudget(limit=64, writable=True)
    for index in range(5):
        budget.decide("Grep", {"pattern": "同一个词", "path": f"d{index}"}, "allow", "")
    # 探索已停止（连续拒绝触发收敛）
    decision, _ = budget.decide("Grep", {"pattern": "同一个词", "path": "x"}, "allow", "")
    assert decision == "deny", "探索类工具应被拒绝"
    # 写入与验证读取仍放行
    decision, _ = budget.decide("Edit", {"file_path": "gathers/TEST/main.py", "old_string": "a", "new_string": "b"}, "allow", "")
    assert decision == "allow", "探索停止后写入必须放行"
    decision, _ = budget.decide("Read", {"file_path": "gathers/TEST/main.py"}, "allow", "")
    assert decision == "allow", "探索停止后验证读取必须放行"


def test_total_budget_exhaustion_still_allows_write():
    """总放行预算耗尽后，写入仍要放行（否则改到一半被拦）。"""
    budget = ToolBudget(limit=2, writable=True)
    budget.decide("Read", {"file_path": "a.py"}, "allow", "")
    budget.decide("Read", {"file_path": "b.py"}, "allow", "")
    decision, _ = budget.decide("Read", {"file_path": "c.py"}, "allow", "")
    assert decision == "deny", "总预算耗尽后探索应停止"
    decision, _ = budget.decide("Edit", {"file_path": "gathers/TEST/main.py", "old_string": "a", "new_string": "b"}, "allow", "")
    assert decision == "allow", "总预算耗尽后写入必须放行"
