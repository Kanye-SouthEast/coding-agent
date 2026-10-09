"""审查 Agent：静态审查代码，输出是否通过 + 修改意见。"""

from __future__ import annotations

import sys

from coding_agent.config import settings
from coding_agent.llm import invoke_json
from coding_agent.state import AgentState, current_task, format_files, format_task
from coding_agent.memory import retrieve_context

REVIEW_PROMPT = """你是资深代码审查员。对照「当前任务」里的签名与契约（contract），审查代码是否实现正确。

审查范围（仅限以下可静态判定的项）：
- 签名是否与契约一致：函数/类名、参数名与类型、返回类型不得私自改动；被依赖任务的签名必须严格复用。
- 非法输入是否按契约处理：contract 里明确列出的非法输入，代码是否抛对应异常 / 返回对应值（只核对「是否写了对应处理」，不验证执行结果）。
- 明显低级错误：语法错误、引用了未定义 / 未 import 的名字、明显拼写错误等。

**明确不属于本关卡职责（不要管，交给 tester 对拍验证）**：
- 合法输入下是否正确产出结果、有无逻辑 bug、边界遗漏、算法错误——这些需要运行 / 推演才能确定，不是静态审查的范围。

否决门槛（重要）：approve=false 前，必须指出一个**具体的、可静态确认的问题**——具体到「代码里哪一处、违反了契约的哪一条 / 犯了什么低级错误」。给不出这种具体问题、只是「感觉可能不对」的，一律 approve。

需求：
{requirement}

当前任务：
{task}

代码文件（仅当前任务刚产出的文件）：
{files}

相关代码（向量检索，可能为空）：
{context}

只输出一个纯净 JSON 对象，包含字段：
- approve: 布尔值（true 表示通过静态审查，false 表示需要修改）
- feedback: 字符串，approve 为 false 时写具体问题，approve 为 true 时写空字符串

输出硬性格式规则：
仅输出纯净 JSON 对象，禁止任何 markdown 代码块（```）、禁止解释/注释、禁止前置或后置文字；输出必须能被 JSON 解析器直接加载。
"""


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "通过")


def reviewer_node(state: AgentState) -> dict:
    if settings.mock:
        # 演示用：静态审查放行，行为 bug 交给 tester 发现
        feedback = ""
    else:
        task = current_task(state)
        print(f"[reviewer] 审查任务 [{task.get('id')}]", flush=True)
        context = retrieve_context(f"{state['requirement']} {task.get('desc', '')}")
        # 只审查当前任务刚产出的文件，避免把其他任务的文件误判成「越界/错误」；
        # 缺记录（老数据）时回退到全量 files 兜底。
        task_key = str(state.get("current_task_index", -1))
        owned = (state.get("task_files") or {}).get(task_key, [])
        review_files = {p: state["files"].get(p, "") for p in owned} if owned else state["files"]
        try:
            data = invoke_json(
                REVIEW_PROMPT.format(
                    requirement=state["requirement"],
                    task=format_task(task),
                    files=format_files(review_files),
                    context=context,
                ),
                model=settings.reviewer_model or None,
            )
        except Exception as e:
            # 审查是「快反馈」关卡，超时/失败时放行，交由 tester 兜底真实正确性
            print(f"[reviewer] 审查调用失败，放行交由测试兜底: {e}", file=sys.stderr)
            return {"review_feedback": "", "review_vetoes": 0, "status": "testing"}
        feedback = "" if _to_bool(data.get("approve")) else data.get("feedback", "存在需要修复的问题")
        if feedback:
            print(f"[reviewer] 审查未通过：{feedback}", file=sys.stderr, flush=True)
    # 否决计数：通过/放行归零；否决 +1。达到 max_review_vetoes 后由路由强制进测试，
    # 避免 reviewer 反复否决却从不跑 tester（对拍），把整个调试预算耗在静态审查上。
    vetoes = state.get("review_vetoes", 0)
    vetoes = 0 if not feedback else vetoes + 1
    return {"review_feedback": feedback, "review_vetoes": vetoes, "status": "testing"}
