"""planner 场景识别 + 计划缓存/语义 few-shot：按需求关键词选择场景片段并注入 prompt。"""

from __future__ import annotations

from coding_agent.config import settings


def test_select_scenario_parser():
    import coding_agent.agents.planner as planner

    assert planner._select_scenario("实现一个命令行计算器")["name"] == "解析器/编译器"
    assert planner._select_scenario("写一个 JSON parser，支持嵌套对象")["name"] == "解析器/编译器"


def test_select_scenario_algorithm():
    import coding_agent.agents.planner as planner

    assert planner._select_scenario("实现两数之和 two sum")["name"] == "算法/数据结构"
    assert planner._select_scenario("实现一个归并排序算法")["name"] == "算法/数据结构"


def test_select_scenario_generic_fallback():
    import coding_agent.agents.planner as planner

    assert planner._select_scenario("写一个读取配置文件的工具")["name"] == "通用"
    assert planner._select_scenario("")["name"] == "通用"
    assert planner._select_scenario(None)["name"] == "通用"


def test_scenario_keyword_matching_is_case_insensitive():
    import coding_agent.agents.planner as planner

    assert planner._select_scenario("Implement a CALCULATOR")["name"] == "解析器/编译器"


def test_planner_injects_scenario_hint(monkeypatch):
    """parser 需求注入「运算符/分层」提示；generic 需求则不注入。"""
    import coding_agent.agents.planner as planner

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")
    monkeypatch.setattr(settings, "planner_fewshot_k", 0)  # 关闭语义 few-shot，隔离 Milvus
    monkeypatch.setattr(planner, "_load_plan_cache", lambda: {})
    monkeypatch.setattr(planner, "_save_plan_cache", lambda cache: None)

    seen = {}

    def fake_json(prompt, model=None, provider=None, timeout=None):
        seen["prompt"] = prompt
        return [{"id": 1, "desc": "a", "deps": [], "status": "pending"}]

    monkeypatch.setattr(planner, "invoke_json", fake_json)

    planner.planner_node({"requirement": "实现一个命令行计算器"})
    assert "解析器/编译器" in seen["prompt"]
    assert "一元负号" in seen["prompt"]  # parser 专属提示已注入

    planner.planner_node({"requirement": "写一个读取配置文件的工具"})
    assert "一元负号" not in seen["prompt"]  # generic 不注入 parser 专属提示


def test_format_fewshot_embeds_plan_json():
    import coding_agent.agents.planner as planner

    out = planner._format_fewshot(
        [{"requirement": "two sum", "plan": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}]}]
    )
    assert "two sum" in out
    assert "不要照抄" in out  # 明确提示历史计划只作参考
    assert '"desc": "a"' in out  # 计划以 JSON 内联进 prompt


def test_planner_cache_hit_skips_milvus(monkeypatch):
    """命中精确缓存时直接复用，不检索 Milvus（确定性快路径，few-shot 不破坏缓存键）。"""
    import coding_agent.agents.planner as planner

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")
    monkeypatch.setattr(settings, "planner_fewshot_k", 2)

    base = planner.PLAN_PROMPT.format(
        requirement="实现一个命令行计算器",
        language=planner.get_runtime(settings.language)["label"],
        scenario_hint=planner._select_scenario("实现一个命令行计算器")["hint"],
        fewshot="",
    )
    key = planner._plan_cache_key(base, settings.planner_model or "", settings.planner_provider or "")
    monkeypatch.setattr(
        planner,
        "_load_plan_cache",
        lambda: {key: [{"id": 1, "desc": "cached", "deps": [], "status": "pending"}]},
    )
    monkeypatch.setattr(planner, "_save_plan_cache", lambda cache: None)

    called = {"search": False, "json": False}
    monkeypatch.setattr(planner, "search_plans", lambda q, k=3: called.update(search=True) or [])
    monkeypatch.setattr(planner, "invoke_json", lambda *a, **kw: called.update(json=True) or [])

    result = planner.planner_node({"requirement": "实现一个命令行计算器"})
    assert result["tasks"][0]["desc"] == "cached"
    assert not called["search"] and not called["json"]


def test_planner_miss_path_injects_fewshot_and_indexes(monkeypatch):
    """缓存未命中：从 Milvus 检索相近计划注入 prompt 作 few-shot，并把新计划写回 Milvus。"""
    import coding_agent.agents.planner as planner

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")
    monkeypatch.setattr(settings, "planner_fewshot_k", 2)
    monkeypatch.setattr(planner, "_load_plan_cache", lambda: {})
    monkeypatch.setattr(planner, "_save_plan_cache", lambda cache: None)

    seen = {}

    def fake_json(prompt, model=None, provider=None, timeout=None):
        seen["prompt"] = prompt
        return [{"id": 1, "desc": "x", "deps": [], "status": "pending"}]

    monkeypatch.setattr(planner, "invoke_json", fake_json)

    monkeypatch.setattr(
        planner,
        "search_plans",
        lambda q, k=3: [
            {
                "requirement": "实现一个简单计算器",
                "plan": [{"id": 1, "desc": "tokenize", "deps": [], "status": "pending"}],
            }
        ],
    )
    indexed = []
    monkeypatch.setattr(planner, "index_plan", lambda req, plan: indexed.append((req, plan)) or 1)

    planner.planner_node({"requirement": "实现一个命令行计算器"})

    assert "相近需求" in seen["prompt"]  # few-shot 已注入
    assert "实现一个简单计算器" in seen["prompt"]
    assert indexed == [("实现一个命令行计算器", [{"id": 1, "desc": "x", "deps": [], "status": "pending"}])]
