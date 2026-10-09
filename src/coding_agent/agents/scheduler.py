"""调度节点：在任务之间推进，标记完成并挑选下一个就绪任务（按依赖）。"""

from __future__ import annotations

from coding_agent.state import AgentState


def _first_ready(tasks: list[dict], done_ids: set) -> int:
    """返回第一个 status=pending 且依赖已全部完成的任务索引；没有则 -1。"""
    for i, t in enumerate(tasks):
        if t.get("status") == "pending" and all(d in done_ids for d in t.get("deps", [])):
            return i
    return -1


def scheduler_node(state: AgentState) -> dict:
    tasks = [dict(t) for t in state["tasks"]]
    done_ids = {t["id"] for t in tasks if t.get("status") == "done"}

    # 当前任务结果：通过则标记 done；达到重试上限仍失败则标记 failed（终态，避免无限重试）
    cur = state.get("current_task_index", -1)
    if cur >= 0:
        tr = state.get("test_result", "")
        if tr.startswith("PASS"):
            tasks[cur]["status"] = "done"
            done_ids.add(tasks[cur]["id"])
        elif tr.startswith("FAIL"):
            tasks[cur]["status"] = "failed"

    nxt = _first_ready(tasks, done_ids)
    if nxt < 0:
        # 无就绪任务：把被失败依赖卡住的 pending 任务标记为 blocked，并据实给出终态
        failed_ids = {t["id"] for t in tasks if t.get("status") == "failed"}
        for t in tasks:
            if t.get("status") == "pending" and any(d in failed_ids for d in t.get("deps", [])):
                t["status"] = "blocked"
        if any(t.get("status") != "done" for t in tasks):
            print("[scheduler] 流程结束：存在失败或受阻任务", flush=True)
            return {"tasks": tasks, "current_task_index": -1, "status": "failed"}
        print("[scheduler] 全部任务完成", flush=True)
        return {"tasks": tasks, "current_task_index": -1, "status": "done"}

    print(f"[scheduler] 下一个任务 [{tasks[nxt]['id']}] {tasks[nxt]['desc']}", flush=True)
    return {
        "tasks": tasks,
        "current_task_index": nxt,
        "iteration": 0,  # 每个任务独立的调试预算
        "review_feedback": "",
        "review_vetoes": 0,  # 每个任务独立的 reviewer 否决计数
        "test_result": "",
        "same_fail_streak": 0,  # 每个任务独立的无进展计数
        "status": "coding",
    }
