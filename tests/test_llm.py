"""LLM 调用韧性：瞬时错误重试、非瞬时不重试、重试耗尽抛出、reviewer 降级放行。"""

from __future__ import annotations

import time

import pytest

from coding_agent.config import settings
from coding_agent.llm import invoke_json, invoke_llm


class _FakeTimeout(Exception):
    pass


def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


def test_invoke_llm_retries_transient(monkeypatch):
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    class Model:
        def invoke(self, prompt):
            calls["n"] += 1
            if calls["n"] < 2:
                raise _FakeTimeout("slow")
            return type("R", (), {"content": "ok"})()

    monkeypatch.setattr(
        "coding_agent.llm.get_model", lambda model=None, provider=None, max_tokens=None, timeout=None: Model()
    )
    assert invoke_llm("x") == "ok"
    assert calls["n"] == 2


def test_invoke_llm_raises_non_transient_immediately(monkeypatch):
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    class Model:
        def invoke(self, prompt):
            calls["n"] += 1
            raise ValueError("bad request")

    monkeypatch.setattr(
        "coding_agent.llm.get_model", lambda model=None, provider=None, max_tokens=None, timeout=None: Model()
    )
    with pytest.raises(ValueError):
        invoke_llm("x")
    assert calls["n"] == 1  # 非瞬时错误不重试


def test_invoke_llm_raises_after_attempts(monkeypatch):
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    class Model:
        def invoke(self, prompt):
            calls["n"] += 1
            raise _FakeTimeout("slow")

    monkeypatch.setattr(
        "coding_agent.llm.get_model", lambda model=None, provider=None, max_tokens=None, timeout=None: Model()
    )
    with pytest.raises(_FakeTimeout):
        invoke_llm("x", attempts=2)
    assert calls["n"] == 2


def test_reviewer_degrades_on_llm_failure(monkeypatch):
    import coding_agent.agents.reviewer as rev

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(rev, "retrieve_context", lambda q: "")

    def boom(prompt, model=None, provider=None):
        raise _FakeTimeout("slow")

    monkeypatch.setattr(rev, "invoke_json", boom)
    state = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
    }
    out = rev.reviewer_node(state)
    assert out["review_feedback"] == ""
    assert out["status"] == "testing"


def test_reviewer_counts_vetoes(monkeypatch):
    import coding_agent.agents.reviewer as rev

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(rev, "retrieve_context", lambda q: "")
    monkeypatch.setattr(
        rev, "invoke_json", lambda p, model=None, provider=None: {"approve": False, "feedback": "有 bug"}
    )
    state = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
        "review_vetoes": 1,
    }
    out = rev.reviewer_node(state)
    assert out["review_feedback"] == "有 bug"
    assert out["review_vetoes"] == 2  # 否决 +1


def test_reviewer_resets_vetoes_on_approve(monkeypatch):
    import coding_agent.agents.reviewer as rev

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(rev, "retrieve_context", lambda q: "")
    monkeypatch.setattr(rev, "invoke_json", lambda p, model=None, provider=None: {"approve": True, "feedback": ""})
    state = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
        "review_vetoes": 3,
    }
    out = rev.reviewer_node(state)
    assert out["review_feedback"] == ""
    assert out["review_vetoes"] == 0  # 通过归零


def test_tester_degrades_on_llm_failure(monkeypatch):
    import coding_agent.agents.tester as tester

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")

    def boom(prompt, model=None, provider=None, max_tokens=None):
        raise _FakeTimeout("slow")

    monkeypatch.setattr(tester, "invoke_json", boom)
    state = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
    }
    out = tester.tester_node(state)
    assert out["test_result"].startswith("FAIL: [tester]")
    assert out["status"] == "testing"  # 测试器自身故障 → 重试测试，而非回退 coder


def test_planner_degrades_on_llm_failure(monkeypatch):
    import coding_agent.agents.planner as planner

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")

    def boom(prompt, model=None, provider=None, timeout=None):
        raise _FakeTimeout("slow")

    monkeypatch.setattr(planner, "invoke_json", boom)
    out = planner.planner_node({"requirement": "r"})
    assert out["tasks"] == [{"id": 1, "desc": "r", "deps": [], "status": "pending"}]
    assert out["status"] == "planning"


def test_planner_degrades_on_empty_tasks(monkeypatch):
    import coding_agent.agents.planner as planner

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")
    monkeypatch.setattr(planner, "invoke_json", lambda p, model=None, provider=None, timeout=None: [])
    out = planner.planner_node({"requirement": "r"})
    assert out["tasks"] == [{"id": 1, "desc": "r", "deps": [], "status": "pending"}]


def test_planner_uses_configured_provider(monkeypatch):
    import coding_agent.agents.planner as planner

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")
    monkeypatch.setattr(settings, "planner_provider", "deepseek")
    seen = {}

    def fake_json(prompt, model=None, provider=None, timeout=None):
        seen["provider"] = provider
        return [{"id": 1, "desc": "a", "deps": [], "status": "pending"}]

    monkeypatch.setattr(planner, "invoke_json", fake_json)
    planner.planner_node({"requirement": "r"})
    assert seen["provider"] == "deepseek"


def test_planner_caches_and_reuses_plan(monkeypatch):
    """同一需求第二次规划命中缓存，不再调用 LLM，返回与首次一致的任务。"""
    import coding_agent.agents.planner as planner

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")
    calls = {"n": 0}

    def fake_json(prompt, model=None, provider=None, timeout=None):
        calls["n"] += 1
        return [
            {"id": 1, "desc": "a", "deps": [], "status": "pending"},
            {"id": 2, "desc": "b", "deps": [1], "status": "pending"},
        ]

    monkeypatch.setattr(planner, "invoke_json", fake_json)
    first = planner.planner_node({"requirement": "r"})
    second = planner.planner_node({"requirement": "r"})
    assert calls["n"] == 1  # 第二次命中缓存，未再调用 LLM
    assert first["tasks"] == second["tasks"]
    assert [t["id"] for t in first["tasks"]] == [1, 2]


def test_planner_cache_key_distinguishes_requirement(monkeypatch):
    """不同需求 → 不同缓存键 → 各调一次 LLM，互不串扰。"""
    import coding_agent.agents.planner as planner

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")
    calls = {"n": 0}

    def fake_json(prompt, model=None, provider=None, timeout=None):
        calls["n"] += 1
        return [{"id": 1, "desc": "x", "deps": [], "status": "pending"}]

    monkeypatch.setattr(planner, "invoke_json", fake_json)
    planner.planner_node({"requirement": "r1"})
    planner.planner_node({"requirement": "r2"})
    assert calls["n"] == 2


def test_planner_does_not_cache_fallback(monkeypatch):
    """LLM 失败走兜底时不写缓存，下次仍会重试 LLM。"""
    import coding_agent.agents.planner as planner

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")
    calls = {"n": 0}

    def fake_json(prompt, model=None, provider=None, timeout=None):
        calls["n"] += 1
        raise _FakeTimeout("slow")

    monkeypatch.setattr(planner, "invoke_json", fake_json)
    planner.planner_node({"requirement": "r"})
    planner.planner_node({"requirement": "r"})
    assert calls["n"] == 2  # 兜底不缓存，两次都重试


def test_invoke_json_retries_on_bad_json(monkeypatch):
    calls = {"n": 0}

    def fake_llm(prompt, model=None, provider=None, max_tokens=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 2:
            return "not json at all"
        return '{"ok": true}'

    monkeypatch.setattr("coding_agent.llm.invoke_llm", fake_llm)
    assert invoke_json("q") == {"ok": True}
    assert calls["n"] == 2


def test_invoke_json_raises_after_attempts(monkeypatch):
    calls = {"n": 0}

    def fake_llm(prompt, model=None, provider=None, max_tokens=None, timeout=None):
        calls["n"] += 1
        return "still not json"

    monkeypatch.setattr("coding_agent.llm.invoke_llm", fake_llm)
    with pytest.raises(ValueError):
        invoke_json("q", attempts=2)
    assert calls["n"] == 2


def test_invoke_json_forwards_model(monkeypatch):
    seen = {}

    def fake_llm(prompt, model=None, provider=None, max_tokens=None, timeout=None):
        seen["model"] = model
        return '{"ok": true}'

    monkeypatch.setattr("coding_agent.llm.invoke_llm", fake_llm)
    assert invoke_json("q", model="Qwen/x") == {"ok": True}
    assert seen["model"] == "Qwen/x"


def test_invoke_json_forwards_provider(monkeypatch):
    seen = {}

    def fake_llm(prompt, model=None, provider=None, max_tokens=None, timeout=None):
        seen["provider"] = provider
        return '{"ok": true}'

    monkeypatch.setattr("coding_agent.llm.invoke_llm", fake_llm)
    assert invoke_json("q", provider="deepseek") == {"ok": True}
    assert seen["provider"] == "deepseek"


def test_invoke_json_forwards_max_tokens(monkeypatch):
    seen = {}

    def fake_llm(prompt, model=None, provider=None, max_tokens=None, timeout=None):
        seen["max_tokens"] = max_tokens
        return '{"ok": true}'

    monkeypatch.setattr("coding_agent.llm.invoke_llm", fake_llm)
    assert invoke_json("q", max_tokens=4096) == {"ok": True}
    assert seen["max_tokens"] == 4096


def test_get_model_sets_max_tokens(monkeypatch):
    import coding_agent.llm as llm

    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    llm.get_model(provider="siliconflow", max_tokens=4096)
    assert captured.get("max_tokens") == 4096


def test_get_model_defaults_max_tokens_to_zero(monkeypatch):
    import coding_agent.llm as llm

    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setattr(settings, "max_tokens", 0)
    llm.get_model(provider="siliconflow")
    # max_tokens 为 0 时不注入该参数，交给模型默认
    assert "max_tokens" not in captured


def test_get_model_sets_timeout(monkeypatch):
    import coding_agent.llm as llm

    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    llm.get_model(provider="siliconflow", timeout=600)
    assert captured.get("timeout") == 600


def test_extract_content_falls_back_to_reasoning_content():
    import coding_agent.llm as llm

    # 常规模型：content 非空直接返回
    assert llm._extract_content(type("R", (), {"content": "ok"})()) == "ok"
    # 推理模型：content 为空，答案落在 additional_kwargs.reasoning_content
    assert (
        llm._extract_content(type("R", (), {"content": "", "additional_kwargs": {"reasoning_content": "thought"}})())
        == "thought"
    )
    # content 为空且无 reasoning_content → 空串（上游会走 JSON 解析失败降级）
    assert llm._extract_content(type("R", (), {"content": ""})()) == ""


def test_invoke_json_forwards_timeout(monkeypatch):
    seen = {}

    def fake_llm(prompt, model=None, provider=None, max_tokens=None, timeout=None):
        seen["timeout"] = timeout
        return '{"ok": true}'

    monkeypatch.setattr("coding_agent.llm.invoke_llm", fake_llm)
    assert invoke_json("q", timeout=600) == {"ok": True}
    assert seen["timeout"] == 600


def test_reviewer_uses_configured_model(monkeypatch):
    import coding_agent.agents.reviewer as rev

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "reviewer_model", "Qwen/fast")
    monkeypatch.setattr(rev, "retrieve_context", lambda q: "")
    seen = {}

    def fake_json(prompt, model=None):
        seen["model"] = model
        return {"approve": True, "feedback": ""}

    monkeypatch.setattr(rev, "invoke_json", fake_json)
    state = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
    }
    rev.reviewer_node(state)
    assert seen["model"] == "Qwen/fast"


def test_tester_uses_configured_model(monkeypatch):
    import coding_agent.agents.tester as tester

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")
    monkeypatch.setattr(settings, "tester_model", "Qwen/fast")
    seen = {}

    def fake_json(prompt, model=None, max_tokens=None):
        seen["model"] = model
        return {"path": "test_t.py", "code": "print('PASS')"}

    monkeypatch.setattr(tester, "invoke_json", fake_json)
    monkeypatch.setattr(tester, "write_file", lambda ws, p, c: None)
    monkeypatch.setattr(tester, "run_command", lambda ws, cmd, language="": (0, "PASS"))
    state = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
    }
    out = tester.tester_node(state)
    assert seen["model"] == "Qwen/fast"
    assert out["test_result"] == "PASS"


def test_tester_resets_vetoes_and_counts_streak(monkeypatch):
    import coding_agent.agents.tester as tester

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")
    monkeypatch.setattr(
        tester,
        "invoke_json",
        lambda p, model=None, max_tokens=None: {"path": "test_t.py", "code": "print('PASS')"},
    )
    monkeypatch.setattr(tester, "write_file", lambda ws, p, c: None)
    monkeypatch.setattr(tester, "run_command", lambda ws, cmd, language="": (1, "boom"))

    base = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
        "review_vetoes": 5,
    }
    out1 = tester.tester_node(base)
    assert out1["review_vetoes"] == 0  # 测试跑过即复位连续否决计数
    assert out1["same_fail_streak"] == 1
    assert out1["test_result"] == "FAIL: boom"

    # 同样失败再来一次 → streak 累计
    out2 = tester.tester_node({**base, "test_result": out1["test_result"], "same_fail_streak": 1})
    assert out2["same_fail_streak"] == 2

    # 通过 → streak 归零
    monkeypatch.setattr(tester, "run_command", lambda ws, cmd, language="": (0, "PASS"))
    out3 = tester.tester_node({**base, "test_result": "FAIL: boom", "same_fail_streak": 2})
    assert out3["same_fail_streak"] == 0
    assert out3["test_result"] == "PASS"


def test_tester_uses_max_tokens_budget(monkeypatch):
    # 回归：tester 生成的测试脚本较长，默认输出预算下易被截断导致 JSON 解析失败，
    # 需把 tester_max_tokens 传给 invoke_json。
    import coding_agent.agents.tester as tester

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")
    seen = {}

    def fake_json(prompt, model=None, max_tokens=None):
        seen["max_tokens"] = max_tokens
        return {"path": "test_t.py", "code": "print('PASS')"}

    monkeypatch.setattr(tester, "invoke_json", fake_json)
    monkeypatch.setattr(tester, "write_file", lambda ws, p, c: None)
    monkeypatch.setattr(tester, "run_command", lambda ws, cmd, language="": (0, "PASS"))
    state = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
    }
    tester.tester_node(state)
    assert seen["max_tokens"] == settings.tester_max_tokens
