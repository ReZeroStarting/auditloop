"""上下文层测试：截断、压缩、配对完整性。"""

from __future__ import annotations

from auditloop.core.context import (
    SUMMARY_HEADER,
    estimate_tokens,
    fit,
    messages_tokens,
    summarize,
    truncate_output,
)
from auditloop.core.messages import assistant, system, tool_call, tool_result, user


def test_token_estimate_orders_chinese_and_ascii() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好世界") > estimate_tokens("ab cd")
    # 中文按 1 字/token 估，不应出现数量级偏差
    assert 4 <= estimate_tokens("四个汉字") <= 8


def test_truncate_keeps_head_and_tail() -> None:
    text = "A" * 5000 + "B" * 5000
    out = truncate_output(text, 1000)
    assert len(out) < len(text)
    assert out.startswith("A")
    assert out.endswith("B")
    assert "已截断" in out


def test_fit_truncates_long_tool_output() -> None:
    messages = [system("s"), user("u"), assistant(None, [tool_call("x", {}, "c1")]), tool_result("c1", "z" * 50_000)]
    out, info = fit(messages, budget_tokens=10**9, tool_chars=1_000)
    assert info["truncated_outputs"] == 1
    assert len(out[3]["content"]) <= 1_200


def _long_conversation(turns: int = 10) -> list:
    messages = [system("你是助手"), user("把这件事做完")]
    for i in range(turns):
        messages.append(assistant(None, [tool_call("fs_read", {"path": f"f{i}.py"}, f"c{i}")]))
        messages.append(tool_result(f"c{i}", "内容" * 200))
    return messages


def test_fit_compacts_and_marks_summary_as_not_user_input() -> None:
    messages = _long_conversation()
    out, info = fit(messages, budget_tokens=800, keep_recent=4, tool_chars=10_000)
    assert info["compacted"] is True
    assert info["dropped_messages"] > 0
    assert info["tokens_after"] < info["tokens_before"]
    assert out[0]["role"] == "system"
    assert out[1]["content"] == "把这件事做完", "用户原始要求必须保留，不能被压掉"
    assert any(m["role"] == "user" and str(m["content"]).startswith(SUMMARY_HEADER) for m in out)


def test_compaction_never_orphans_tool_results() -> None:
    messages = _long_conversation()
    out, info = fit(messages, budget_tokens=600, keep_recent=5, tool_chars=10_000)
    assert info["compacted"] is True
    for index, message in enumerate(out):
        if message["role"] == "tool":
            assert out[index - 1]["role"] == "assistant", "tool 消息前面必须紧跟着它的 tool_call"


def test_nothing_to_compact_returns_unchanged() -> None:
    messages = [system("s"), user("u"), assistant("短回答")]
    out, info = fit(messages, budget_tokens=100_000)
    assert info["compacted"] is False
    assert out == messages


def test_summarize_merges_previous_summary_instead_of_nesting() -> None:
    previous = {"role": "user", "content": f"{SUMMARY_HEADER}\n- 用户：早先的要求"}
    text = summarize([previous, {"role": "tool", "content": "观察结果"}])
    assert "用户：用户" not in text
    assert "早先的要求" in text
    assert "观察结果" in text


def test_messages_tokens_grows_with_content() -> None:
    assert messages_tokens([user("短")]) < messages_tokens([user("长" * 500)])
