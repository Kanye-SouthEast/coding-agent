"""编码 Agent：根据当前任务与反馈生成代码并写入工作区。"""

from __future__ import annotations

import sys

from coding_agent.config import settings
from coding_agent.llm import invoke_json
from coding_agent.runtimes import get_runtime
from coding_agent.state import AgentState, current_task, files_for_task, format_files, format_task
from coding_agent.tools.files import write_file
from coding_agent.memory import index_files, retrieve_context

CODE_PROMPT = """你是资深软件工程师。根据需求和已有反馈，完成下面这个任务。

需求：
{requirement}

当前任务：
{task_brief}

已有文件：
{files}

相关代码（向量检索，可能为空）：
{context}

审查反馈（可能为空）：
{feedback}

最近测试结果（可能为空）：
{test_result}

所有代码一律使用 {language} 实现。{code_hint}

只输出一个纯净 JSON 对象，包含字段 files：文件数组，每个元素含 path（要写入的文件路径）、code（该文件完整内容）。
一个任务可以写多个文件（例如 C++ 拆成 .h 声明 + .cpp 实现），也可以只写一个文件。
只实现当前任务要求的代码，可以 import / #include 已有文件里的函数。
严格按「当前任务」里的签名实现，禁止改函数/类名、参数、返回类型；非法输入必须按契约（contract）抛对应异常或返回对应值，不得擅自放宽或收紧。
入口（main/CLI）由专门的任务负责，本任务只写契约里要求的文件，不要提前写入口文件。

输出硬性格式规则：
仅输出纯净 JSON 对象，禁止任何 markdown 代码块（```）、禁止解释/注释、禁止前置或后置文字；输出必须能被 JSON 解析器直接加载。
"""

# mock 演示用代码
BUGGY_FIB = "def fib(n):\n    return n\n"
FIB = "def fib(n):\n    if n <= 1:\n        return n\n    return fib(n - 1) + fib(n - 2)\n"
FIB_LIST = "from task1 import fib\n\ndef fib_list(n):\n    return [fib(i) for i in range(n)]\n"
FIB_SUM = "from task2 import fib_list\n\ndef fib_sum(n):\n    return sum(fib_list(n))\n"


def _mock_code(task_id: int, iteration: int) -> tuple[str, str]:
    if task_id == 1:
        # 任务 1 第一次故意写错，展示调试闭环
        return "task1.py", (BUGGY_FIB if iteration == 1 else FIB)
    if task_id == 2:
        return "task2.py", FIB_LIST
    if task_id == 3:
        return "task3.py", FIB_SUM
    return "main.py", FIB


def _python_syntax_errors(files: dict[str, str]) -> str:
    """对 Python 文件做 compile() 语法检查，返回错误摘要（无错返回空串）。"""
    problems = []
    for path, code in files.items():
        if not path.endswith(".py"):
            continue
        try:
            compile(code, path, "exec")
        except SyntaxError as e:
            problems.append(f"{path}:{e.lineno}: {e.msg}")
    return "\n".join(problems)


def coder_node(state: AgentState) -> dict:
    iteration = state["iteration"] + 1
    task = current_task(state)
    runtime = get_runtime(settings.language)
    print(f"[coder] 任务 [{task.get('id')}] {task.get('desc', '')}（第 {iteration} 轮）", flush=True)

    if settings.mock:
        path, code = _mock_code(task.get("id"), iteration)
        new_files = {path: code}
    else:
        context = retrieve_context(f"{state['requirement']} {task.get('desc', '')}")
        new_files = {}
        syntax_feedback = ""
        for attempt in range(settings.coder_syntax_retries):
            data = invoke_json(
                CODE_PROMPT.format(
                    requirement=state["requirement"],
                    task_brief=format_task(task),
                    files=format_files(files_for_task(state)),
                    context=context,
                    feedback=state.get("review_feedback", "") + syntax_feedback,
                    test_result=state.get("test_result", ""),
                    language=runtime["label"],
                    code_hint=runtime["code_hint"],
                ),
                model=settings.coder_model or None,
                provider=settings.coder_provider or None,
                timeout=settings.coder_timeout or None,
            )
            entries = data.get("files")
            if entries is None:
                # 兜底：模型若仍按单文件 {path, code} 输出，也接受
                entries = [{"path": data.get("path"), "code": data.get("code")}]
            new_files = {}
            for entry in entries:
                p, c = entry.get("path"), entry.get("code")
                if p and c:
                    new_files[p] = c

            errors = _python_syntax_errors(new_files)
            if not errors:
                break
            print(
                f"[coder] 语法错误（第 {attempt + 1} 次生成，自修复重试）：\n{errors}",
                file=sys.stderr,
                flush=True,
            )
            syntax_feedback = "\n\n【重要】上一次生成的代码有语法错误，请修复后再输出，错误如下：\n" + errors
        else:
            print("[coder] 语法自修复次数耗尽，交由审查/测试兜底", file=sys.stderr, flush=True)

    # 工作区清理：本任务上一次尝试写过的文件，若这次不再产出（如改了文件名 tokenize.py→tokenizer.py），
    # 从磁盘与 files 里删除，避免残留孤儿文件被误 import 导致结果分叉。
    task_files = {k: list(v) for k, v in state.get("task_files", {}).items()}
    task_key = str(state.get("current_task_index", -1))
    owned = set(task_files.get(task_key, []))
    stale = owned - set(new_files)
    for p in stale:
        try:
            (settings.workspace / p).unlink(missing_ok=True)
        except OSError:
            pass

    for p, c in new_files.items():
        write_file(settings.workspace, p, c)
    if not settings.mock:
        index_files(new_files)  # 索引新写的文件，供后续任务检索

    files = {k: v for k, v in {**state["files"], **new_files}.items() if k not in stale}
    task_files[task_key] = sorted(new_files)
    return {
        "iteration": iteration,
        "files": files,
        "task_files": task_files,
        "status": "reviewing",
    }
