"""全局共享状态：LangGraph 各节点之间传递的唯一状态。"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]  # 全局对话 / 轨迹
    requirement: str                          # 原始需求
    tasks: list[dict[str, Any]]               # 任务列表 [{id, desc, deps, status}]；status ∈ pending|done|failed|blocked
    current_task_index: int                   # 当前任务索引，-1 表示无
    files: dict[str, str]                     # 文件内容 {path: content}
    review_feedback: str                      # 审查意见（空串表示通过）
    review_vetoes: int                        # 本任务内 reviewer 连续否决次数（跑过一次测试后复位；达到阈值后强制进测试，防止饿死 tester）
    test_result: str                          # 最近一次测试输出
    same_fail_streak: int                     # 连续完全相同的测试失败次数（无进展熔断，达到阈值提前标 failed）
    task_files: dict[str, list[str]]          # 每个任务拥有的文件路径 {task_index: [paths]}，用于清理改名后残留的孤儿文件
    iteration: int                            # 当前任务的编码尝试次数
    status: str                               # 当前阶段：planning|coding|reviewing|testing|done|failed


def current_task(state: AgentState) -> dict[str, Any]:
    """返回当前任务字典，无任务时返回空 dict。"""
    idx = state.get("current_task_index", -1)
    tasks = state.get("tasks", [])
    if 0 <= idx < len(tasks):
        return tasks[idx]
    return {}


def format_task(task: dict[str, Any]) -> str:
    """把任务渲染成给 coder/tester 看的可读文本，突出签名与接口契约（缺失字段则省略）。

    planner 会在 desc 之外补充 signature（精确签名）和 contract（输入输出/边界）；
    老数据（mock / 降级单任务）没有这些字段，这里用 .get 兜底，不会 KeyError。
    """
    lines = [f"- 描述：{task.get('desc', '')}"]
    signature = task.get("signature", "")
    if signature:
        lines.append(f"- 签名：{signature}")
    contract = task.get("contract", "")
    if contract:
        lines.append(f"- 输入输出/边界：{contract}")
    invalid_inputs = task.get("invalid_inputs")
    if invalid_inputs:
        lines.append(f"- 非法输入清单（测试必须逐条照抄、不得增删）：{invalid_inputs}")
    deps = task.get("deps", [])
    if deps:
        lines.append(f"- 依赖的前置任务：{deps}")
    return "\n".join(lines)


def format_files(files: dict[str, str]) -> str:
    """把 {路径: 内容} 渲染成带围栏的可读代码清单，供 coder/reviewer/tester 拼进 prompt。

    直接用 str(dict) 会输出 `{'a.py': 'def f...\\n...'}` 这样的转义噪声，模型易读错；
    这里逐文件用 `### 路径 + 代码围栏` 列出，让模型能清楚看到每个文件的确切内容与可 import 的签名。
    空文件列表返回占位文案，避免 prompt 里出现空块。
    """
    if not files:
        return "（暂无）"
    blocks = [f"### {path}\n```\n{content or ''}\n```" for path, content in files.items()]
    return "\n\n".join(blocks)


def files_for_task(state: AgentState) -> dict[str, str]:
    """返回「当前任务 + 其传递依赖」已产出的文件，供 coder/tester 注入 prompt。

    依赖闭包：任务的 deps 存的是 task id（不是列表下标），所以先建 id→index 映射，
    再从当前任务沿 deps 反向做传递闭包，取这些任务 task_files 的并集。目的：只注入
    当前任务真正会 import/#include 的依赖文件，而非全量 files，减少噪声与 token 消耗。

    兜底（方向性安全，宁多勿少——漏给文件会让 coder/tester 看不到本应可见的代码，
    比多给更危险）：
    - 无 task_files 记录（老数据）或 current_task_index 越界 → 回退全量 files，保持旧行为；
    - 闭包内没有任何已记录文件（如无依赖的首任务、当前任务首次编码）且 files 非空
      → 回退全量，避免少给上下文。
    """
    files = state.get("files", {})
    task_files = state.get("task_files") or {}
    if not task_files:
        return files
    idx = state.get("current_task_index", -1)
    tasks = state.get("tasks", [])
    if not (0 <= idx < len(tasks)):
        return files

    id_to_idx = {t.get("id"): i for i, t in enumerate(tasks) if t.get("id") is not None}
    closure: set[int] = set()
    stack = [idx]
    while stack:
        i = stack.pop()
        if i in closure:
            continue
        closure.add(i)
        for dep_id in tasks[i].get("deps") or []:
            j = id_to_idx.get(dep_id)
            if j is not None:
                stack.append(j)

    paths: set[str] = set()
    for i in closure:
        paths.update(task_files.get(str(i), []))

    pruned = {p: files.get(p, "") for p in paths if p in files}
    if not pruned and files:
        return files  # 闭包空但有历史文件：宁多勿少，回退全量
    return pruned
