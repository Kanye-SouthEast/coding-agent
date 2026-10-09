"""图路由 + 端到端 mock 闭环：条件边路由函数 + 完整图在 mock 模式下跑通调试闭环。"""

from __future__ import annotations

from coding_agent.config import settings
from coding_agent.graph import (
    build_graph,
    route_after_reviewer,
    route_after_scheduler,
    route_after_tester,
)


def make_state(requirement: str = "fib") -> dict:
    return {
        "messages": [],
        "requirement": requirement,
        "tasks": [],
        "current_task_index": -1,
        "files": {},
        "review_feedback": "",
        "test_result": "",
        "iteration": 0,
        "status": "planning",
    }


def test_route_after_scheduler():
    assert route_after_scheduler({"current_task_index": 0}) == "coder"
    assert route_after_scheduler({"current_task_index": -1}) == "done"


def test_route_after_reviewer():
    assert route_after_reviewer({"review_feedback": "有 bug", "iteration": 0}) == "coder"
    assert route_after_reviewer({"review_feedback": "", "iteration": 0}) == "tester"
    # 达到迭代上限后即使有意见也不再回退
    assert (
        route_after_reviewer({"review_feedback": "有 bug", "iteration": settings.max_iterations})
        == "tester"
    )


def test_route_after_reviewer_veto_cap_forces_test():
    # reviewer 否决达到阈值 → 强制进测试，避免饿死 tester（对拍）
    assert (
        route_after_reviewer({"review_feedback": "有 bug", "iteration": 0, "review_vetoes": 1})
        == "coder"
    )
    assert (
        route_after_reviewer(
            {
                "review_feedback": "有 bug",
                "iteration": 0,
                "review_vetoes": settings.max_review_vetoes,
            }
        )
        == "tester"
    )


def test_route_after_tester():
    assert route_after_tester({"test_result": "FAIL: boom", "iteration": 0}) == "coder"
    assert route_after_tester({"test_result": "PASS: ok", "iteration": 0}) == "scheduler"
    # 达到迭代上限后测试失败也不再回退
    assert (
        route_after_tester({"test_result": "FAIL: boom", "iteration": settings.max_iterations})
        == "scheduler"
    )


def test_route_after_tester_retries_on_infra_failure():
    # 测试器自身故障（非被测代码 bug）应回 tester 重试，而非回退 coder
    assert route_after_tester({"test_result": "FAIL: [tester] ValueError: x", "iteration": 0}) == "tester"
    # 迭代耗尽后同样落到 scheduler 终止，避免死循环
    assert (
        route_after_tester(
            {"test_result": "FAIL: [tester] ValueError: x", "iteration": settings.max_iterations}
        )
        == "scheduler"
    )


def test_route_after_tester_fuses_on_no_progress():
    # 连续 max_same_failures 次相同失败 → 提前交 scheduler（放弃），不再回 coder
    assert (
        route_after_tester(
            {
                "test_result": "FAIL: boom",
                "iteration": 0,
                "same_fail_streak": settings.max_same_failures,
            }
        )
        == "scheduler"
    )
    # 未达阈值仍回 coder 修复
    assert (
        route_after_tester(
            {
                "test_result": "FAIL: boom",
                "iteration": 0,
                "same_fail_streak": settings.max_same_failures - 1,
            }
        )
        == "coder"
    )


def test_tester_self_loop_is_wired():
    # 回归：route_after_tester 会返回 "tester"（测试器自身故障重试），
    # 若条件边映射漏掉 tester→tester，运行时会 KeyError: 'tester'。
    edges = {(e.source, e.target) for e in build_graph().get_graph().edges}
    assert ("tester", "tester") in edges


def test_full_mock_graph_runs_debug_loop():
    app = build_graph()
    result = app.invoke(make_state("fibonacci"), config={"configurable": {"thread_id": "t1"}})

    assert result["status"] == "done"
    assert [t["status"] for t in result["tasks"]] == ["done", "done", "done"]
    assert "task1.py" in result["files"]
    assert "task3.py" in result["files"]
    # 调试闭环：任务 1 第一次写出 buggy fib，测试失败回退重写，最终为正确实现
    assert "return fib(n - 1) + fib(n - 2)" in result["files"]["task1.py"]
