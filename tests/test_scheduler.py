"""调度依赖：挑选就绪任务、标记完成、推进下一个、全部完成结束、失败不推进。"""

from __future__ import annotations

from coding_agent.agents.scheduler import _first_ready, scheduler_node


def test_first_ready_respects_dependencies():
    tasks = [
        {"id": 1, "desc": "a", "deps": [], "status": "pending"},
        {"id": 2, "desc": "b", "deps": [1], "status": "pending"},
        {"id": 3, "desc": "c", "deps": [2], "status": "pending"},
    ]
    assert _first_ready(tasks, set()) == 0
    tasks[0]["status"] = "done"
    assert _first_ready(tasks, {1}) == 1
    tasks[1]["status"] = "done"
    assert _first_ready(tasks, {1, 2}) == 2
    tasks[2]["status"] = "done"
    assert _first_ready(tasks, {1, 2, 3}) == -1


def test_first_ready_skips_blocked():
    tasks = [
        {"id": 1, "desc": "a", "deps": [9], "status": "pending"},  # 被未完成的 9 阻塞
        {"id": 2, "desc": "b", "deps": [], "status": "pending"},  # 就绪
    ]
    assert _first_ready(tasks, set()) == 1  # 跳过被阻塞的任务 1，选任务 2


def test_first_ready_skips_done():
    tasks = [
        {"id": 1, "desc": "a", "deps": [], "status": "done"},
        {"id": 2, "desc": "b", "deps": [1], "status": "pending"},
    ]
    assert _first_ready(tasks, {1}) == 1


def test_scheduler_marks_pass_and_advances():
    state = {
        "tasks": [
            {"id": 1, "desc": "a", "deps": [], "status": "pending"},
            {"id": 2, "desc": "b", "deps": [1], "status": "pending"},
        ],
        "current_task_index": 0,
        "test_result": "PASS: ok",
    }
    out = scheduler_node(state)
    assert out["tasks"][0]["status"] == "done"
    assert out["current_task_index"] == 1
    assert out["status"] == "coding"
    assert out["iteration"] == 0
    assert out["review_feedback"] == ""
    assert out["test_result"] == ""


def test_scheduler_done_when_all_complete():
    state = {
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "done"}],
        "current_task_index": 0,
        "test_result": "PASS: ok",
    }
    out = scheduler_node(state)
    assert out["status"] == "done"
    assert out["current_task_index"] == -1


def test_scheduler_failed_test_marks_failed_and_stops():
    state = {
        "tasks": [
            {"id": 1, "desc": "a", "deps": [], "status": "pending"},
            {"id": 2, "desc": "b", "deps": [1], "status": "pending"},
        ],
        "current_task_index": 0,
        "test_result": "FAIL: boom",
    }
    out = scheduler_node(state)
    # 重试上限用尽：任务 1 标记 failed，依赖它的任务 2 标记 blocked，整体失败结束（不再无限重试）
    assert out["tasks"][0]["status"] == "failed"
    assert out["tasks"][1]["status"] == "blocked"
    assert out["current_task_index"] == -1
    assert out["status"] == "failed"


def test_scheduler_failed_task_does_not_block_independent_task():
    state = {
        "tasks": [
            {"id": 1, "desc": "a", "deps": [], "status": "pending"},
            {"id": 2, "desc": "b", "deps": [], "status": "pending"},
        ],
        "current_task_index": 0,
        "test_result": "FAIL: boom",
    }
    out = scheduler_node(state)
    # 任务 1 失败，但任务 2 无依赖，仍应被选中推进
    assert out["tasks"][0]["status"] == "failed"
    assert out["current_task_index"] == 1
    assert out["status"] == "coding"
