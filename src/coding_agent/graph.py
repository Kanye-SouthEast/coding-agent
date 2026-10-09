"""LangGraph 图定义：多任务调度 + 调试闭环。

流程：
    planner → scheduler → coder → reviewer → tester
                ↑  ┌───────────────────────────────┘
                └──┘  审查意见回退 coder；测试通过回 scheduler
                      scheduler 按依赖推进下一个任务，无任务则结束
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from coding_agent.agents.coder import coder_node
from coding_agent.agents.planner import planner_node
from coding_agent.agents.reviewer import reviewer_node
from coding_agent.agents.scheduler import scheduler_node
from coding_agent.agents.tester import tester_node
from coding_agent.config import settings
from coding_agent.state import AgentState


def route_after_scheduler(state: AgentState) -> str:
    return "coder" if state["current_task_index"] >= 0 else "done"


def route_after_reviewer(state: AgentState) -> str:
    """审查有意见、未超迭代上限、且否决未达阈值 → 回 coder 修复；否则进测试。

    关键：reviewer 否决次数达到 `max_review_vetoes` 时强制进测试（对拍），
    避免 reviewer 反复否决却从不跑 tester，把整个调试预算耗在静态审查上
    （曾导致 f09a39a2… 线程 8 轮全耗在 reviewer↔coder，tester 只最后跑了一次）。
    """
    if (
        state.get("review_feedback")
        and state["iteration"] < settings.max_iterations
        and state.get("review_vetoes", 0) < settings.max_review_vetoes
    ):
        return "coder"
    return "tester"


def route_after_tester(state: AgentState) -> str:
    """测试失败且未超上限 → 回 coder 修复；测试器自身故障 → 回 tester 重试；通过则交给 scheduler。

    `FAIL: [tester]` 前缀表示「测试器生成/解析/沙箱」自身出错（不是被测代码的 bug），
    此时回 coder 只会让 coder 白改一轮好代码，正确做法是重试测试器（tester_node 已把
    iteration+1 作上限，耗尽后落到 scheduler 标记 failed）。

    无进展熔断：连续 `max_same_failures` 次完全相同的失败（coder 改了但测试输出一字未变），
    说明 coder 已无法修复，提前交给 scheduler 标 failed，而不是空转到迭代耗尽。
    """
    if state["iteration"] < settings.max_iterations:
        tr = state["test_result"]
        if tr.startswith("FAIL: [tester]"):
            return "tester"
        if tr.startswith("FAIL"):
            if state.get("same_fail_streak", 0) >= settings.max_same_failures:
                return "scheduler"
            return "coder"
    return "scheduler"


def approve_node(state: AgentState) -> dict:
    """人工审批挂载点：不修改状态，仅作为 interrupt_before 的暂停位置。"""
    return {}


def build_graph(checkpointer=None):
    g = StateGraph(AgentState)

    g.add_node("planner", planner_node)
    g.add_node("approve", approve_node)
    g.add_node("scheduler", scheduler_node)
    g.add_node("coder", coder_node)
    g.add_node("reviewer", reviewer_node)
    g.add_node("tester", tester_node)

    g.add_edge(START, "planner")
    g.add_edge("planner", "approve")
    g.add_edge("approve", "scheduler")
    g.add_conditional_edges("scheduler", route_after_scheduler, {"coder": "coder", "done": END})
    g.add_edge("coder", "reviewer")
    g.add_conditional_edges("reviewer", route_after_reviewer, {"coder": "coder", "tester": "tester"})
    g.add_conditional_edges(
        "tester",
        route_after_tester,
        {"coder": "coder", "scheduler": "scheduler", "tester": "tester"},
    )

    # 持久化 checkpointer（SqliteSaver/PostgresSaver）支持断点续跑 / 事后查看状态；
    # 未传入时用 MemorySaver 兜底（便于测试 / 演示）。
    # 规划后暂停（interrupt_before approve），人工批准计划再继续（可 --auto-approve 跳过）。
    interrupt_before = ["approve"] if settings.interrupt_before_plan else None
    return g.compile(
        checkpointer=checkpointer or MemorySaver(),
        interrupt_before=interrupt_before,
    )
