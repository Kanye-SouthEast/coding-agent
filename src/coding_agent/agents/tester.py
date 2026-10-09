"""测试 Agent：生成测试脚本并运行，记录结果。"""

from __future__ import annotations

import re
import sys

from coding_agent.config import settings
from coding_agent.llm import invoke_json
from coding_agent.runtimes import get_runtime
from coding_agent.state import AgentState, current_task, files_for_task, format_files, format_task
from coding_agent.tools.files import write_file
from coding_agent.tools.shell import run_command

# mock 演示用测试：按任务 id 分别验证
MOCK_TESTS = {
    1: '''python -c "from task1 import fib; assert fib(10) == 55; print('PASS')"''',
    2: '''python -c "from task2 import fib_list; assert fib_list(5) == [0, 1, 1, 2, 3]; print('PASS')"''',
    3: '''python -c "from task3 import fib_sum; assert fib_sum(5) == 7; print('PASS')"''',
}

TEST_PROMPT = """你是资深测试工程师。根据需求、当前任务和代码，写一个可独立运行的 {label} 测试脚本。

需求：
{requirement}

当前任务：
{task_brief}

代码文件：
{files}

审查反馈（可能为空，reviewer 的静态审查意见，可能附带具体反例）：
{review_feedback}

上一次尝试的失败原因（可能为空；非空表示上一次生成的输出有问题，请据此修正，不要重复同样的错误）：
{prev_failure}

只输出一个纯净 JSON 对象，包含字段：
- path: 测试脚本文件名（后缀 .{ext}，如 test_check.{ext}）
- code: 完整测试脚本内容。
- invalid_inputs: 字符串数组，你测试脚本里所有「期望抛异常/失败」的非法输入，**逐字照抄**「当前任务」里 contract 的非法输入清单（一条对应一条、不得改写措辞），不得自己增删、不得把别的层（词法/解析/求值）的职责算进来；测试脚本里没有任何非法输入用例时返回空数组 []。

{test_hint}

【硬性要求】被测函数必须从「代码文件」里 import / 引用真实实现，严禁在测试脚本里重新实现被测函数；测试只验证代码文件里的真实代码，import 失败或找不到函数时要让测试直接失败（非零退出/断言失败），不得静默通过。

【审查反例必测】若上面的「审查反馈」给出了具体反例（某个具体输入 + 该输入下契约期望的行为），必须把这个反例作为一条显式测试用例加入，断言被测函数在该输入下的真实行为。注意：审查反例本身可能是错的（reviewer 幻觉），此时按 contract 与代码真实行为如实断言即可——你测的是「代码是否符合 contract」，不是「是否复现 reviewer 的说法」。不要因为有了审查反例就跳过 contract 里其余非法/合法用例。

【重要】不要手写任何期望输出值（很容易算错）。正确做法：
1. 先读「当前任务」里的「输入输出/边界（contract）」——**非法输入清单必须严格照抄 contract、逐条对应，不得自己增删，也不得把 contract 没写为非法的输入判成非法**。只测试本任务负责的那一层，逐层分清：
   - 若本任务是词法/分词（tokenize）：运算符连续（`1++2`、`1+*2`）、数字相邻（`1 2`）、括号不匹配（`(1+2`）、缺操作数/缺运算符（`1+`）等**语法**错误，以及除零等**语义**错误，都属于后续解析/求值任务的职责。词法阶段必须把它们当作**合法的 token 序列**，不得断言其抛异常——除非 contract 白纸黑字写明本任务要拒绝它们。
   - 若本任务是解析/求值：才负责断言上述语法/语义错误。
   - 判据：每一条「期望抛异常/失败」的输入，都必须能在 contract 里逐字找到出处；找不到出处的一律按合法输入处理，不得擅自补成非法用例。
2. 在测试脚本里写一个「暴力参考实现」（brute-force）：用**与被测代码完全不同的算法/思路**（被测是手写状态机，参考就用正则/递归；被测是 Shunting-Yard，参考就用递归下降或别的方式）来解**完全相同的题**。**严禁逐行照抄被测函数**——若参考实现和被测代码几乎一样，对拍就失去独立裁判的意义，永远发现不了同一处错误。务必完整实现题目的全部约束，尤其是「不重复 / 唯一 / 去重」要求：若题目要求结果不重复，暴力实现也必须去重（例如把结果存进 set，或枚举时跳过重复值），不能只枚举所有下标组合。
3. 准备多组输入：随机小规模用例 + 边界用例（空输入、单元素、重复元素、含 0 与负数等，以及 contract 里逐条列出的每一项非法输入）。
4. 对每组**合法**输入，分别调用被测函数与暴力参考实现，比较两者输出。比较时只做「顺序无关」的归一化：把每个结果元素的内部成员排序成 tuple，再对结果列表排序；**不要用 set 去重**（去重是参考实现和被测函数各自的责任，这样才能暴露被测函数漏掉去重的 bug）。
5. 全部一致才打印 PASS；任何不一致立即断言失败 / 返回非零码。

非法输入断言：非法输入**只断言被测函数**抛 contract 指定的异常类型（assertRaises 或 try/except 精确匹配，不能只断「抛了异常」）。**不要**对非法输入调用暴力参考实现，也不要要求它抛异常——暴力参考实现只负责「合法输入」的正确结果对拍，不负责复刻输入校验。

断言失败或抛异常会让进程以非零码退出。

输出硬性格式规则：
仅输出纯净 JSON 对象，禁止任何 markdown 代码块（```）、禁止解释/注释、禁止前置或后置文字；输出必须能被 JSON 解析器直接加载。
"""


def _norm_invalid(s: str) -> str:
    """归一化非法输入条目：去空白 + 小写，供清单对拍做稳健比较。"""
    return re.sub(r"\s+", "", str(s)).lower()


def _extra_invalid_inputs(task: dict, data: dict) -> list[str]:
    """返回 tester 断言了、却不在本任务 contract 非法输入清单里的条目（多出来的）。

    这是「非法输入清单对拍」：tester 每条「期望抛异常」的非法输入都必须能在本任务
    contract 的 invalid_inputs 里找到出处；出现「多出来的」说明 tester 把别的层
    （如把词法层的 .5/1. 拿到解析层）的职责错判到本层，属于测试器生成故障。
    任一方缺字段（旧 planner 数据 / 模型未按格式输出）则跳过，不误伤。
    """
    expected = task.get("invalid_inputs")
    actual = data.get("invalid_inputs")
    if expected is None or actual is None:
        return []
    if isinstance(expected, str):
        expected = [expected]
    if isinstance(actual, str):
        actual = [actual]
    expected_norm = {_norm_invalid(x) for x in expected}
    if not expected_norm:
        # 本任务无非法输入：tester 输出任何非法输入都是「多出来的」
        return list(actual)
    # 归一化后精确匹配：条目必须能在清单里逐字找到出处（prompt 要求 tester 逐字照抄）。
    # 不用子串匹配——'1.' 是 '1.2.3' 的前缀、语义却不同，子串会漏判。
    return [a for a in actual if _norm_invalid(a) not in expected_norm]


def _is_entry_task(task: dict) -> bool:
    """判断任务是否为「可运行入口」任务：signature 含 main( 入口。

    入口任务的 main 是全局入口，无法被测试脚本 import/调用，会与测试脚本自带的
    main 符号冲突；这类任务改用入口专用编译命令（排除入口文件）+ 专用测试提示。
    """
    sig = (task.get("signature") or "").strip()
    return bool(re.search(r"\bmain\s*\(", sig))


def tester_node(state: AgentState) -> dict:
    task = current_task(state)
    runtime = get_runtime(settings.language)
    print(f"[tester] 测试任务 [{task.get('id')}]", flush=True)

    if settings.mock:
        # mock 演示固定 Python，语言设置只影响真实模式
        cmd = MOCK_TESTS.get(task.get("id"), MOCK_TESTS[1])
        code, out = run_command(settings.workspace, cmd, language="python")
    else:
        # 上一次 tester 自身失败（FAIL: [tester] 前缀）的报错，重试时注入 prompt 让模型自我纠正
        prev_result = state.get("test_result", "") or ""
        prev_failure = (
            prev_result[len("FAIL: [tester] "):]
            if prev_result.startswith("FAIL: [tester]")
            else ""
        )
        # 入口任务（signature 含 main()）：注入专用测试提示，改用排除入口文件的编译命令
        is_entry = _is_entry_task(task)
        test_hint = runtime["test_hint"]
        if is_entry and runtime.get("entry_test_hint"):
            test_hint = test_hint + "\n\n" + runtime["entry_test_hint"]
        try:
            data = invoke_json(
                TEST_PROMPT.format(
                    requirement=state["requirement"],
                    task_brief=format_task(task),
                    files=format_files(files_for_task(state)),
                    review_feedback=state.get("review_feedback", ""),
                    prev_failure=prev_failure,
                    label=runtime["label"],
                    ext=runtime["ext"],
                    test_hint=test_hint,
                ),
                model=settings.tester_model or None,
                max_tokens=settings.tester_max_tokens or None,
            )
            # 非法输入清单对拍：tester 断言抛异常的非法输入必须都能在本任务 contract 的
            # invalid_inputs 里找到出处；多出来的说明 tester 把别的层（如词法的 .5/1.）
            # 错判到本层，属于测试器生成故障，走 FAIL: [tester] 回 tester 重试。
            extras = _extra_invalid_inputs(task, data)
            if extras:
                raise ValueError(
                    "非法输入用例超出本任务 contract 清单（可能是错判层级）: "
                    + ", ".join(repr(x) for x in extras)
                )
            test_code = data["code"]
            # 编译型语言的项目脚手架（如 Rust 的 Cargo.toml）先落盘
            for pf_path, pf_content in (runtime.get("project_files") or {}).items():
                write_file(settings.workspace, pf_path, pf_content)
            # 编译型语言用固定测试文件名，避免跨任务残留的测试文件（各自含 main）互相冲突
            test_path = runtime.get("test_file") or data["path"]
            write_file(settings.workspace, test_path, test_code)
            # 生成后先做语法自检：测试脚本自身若无法解析（如漏引号导致 SyntaxError），
            # 属于「测试器生成故障」，应走 FAIL: [tester] 回 tester 重试，而不是当成
            # 「被测代码没过测试」回 coder（coder 改不到测试文件，只会白改一轮）。
            # 目前仅覆盖 Python；编译型语言的编译步已并入 run_test。
            if runtime["ext"] == "py":
                compile(test_code, str(test_path), "exec")
            run_cmd = (
                runtime["entry_run_test"]
                if is_entry and runtime.get("entry_run_test")
                else runtime["run_test"]
            )
            code, out = run_command(
                settings.workspace,
                run_cmd.format(test=test_path),
                language=settings.language,
            )
        except Exception as e:
            # 降级：LLM 生成测试或沙箱运行异常时，不崩溃主流程。
            # 注意区分「测试器自身故障」（如 JSON 解析失败 / 模型超时 / 测试脚本语法错误）与「代码真的没通过测试」：
            # 前者用 `FAIL: [tester]` 标记，路由时回「tester」重试而非回退 coder（回退 coder 对
            # 测试器故障毫无帮助，只会让 coder 白改一轮好代码）；同时 iteration+1 给重试设上限，
            # 耗尽后由 scheduler 标记 failed，不会死循环。
            print(
                f"[tester] 生成/运行测试失败（测试器故障，将重试）: {type(e).__name__}: {e}",
                file=sys.stderr,
                flush=True,
            )
            return {
                "test_result": f"FAIL: [tester] {type(e).__name__}: {e}",
                "status": "testing",
                "iteration": state.get("iteration", 0) + 1,
            }

    if code == 0:
        test_result = "PASS"
        status = "done"
    else:
        test_result = "FAIL: " + (out.strip() or "(无输出)")
        status = "coding"
        print(f"[tester] 测试未通过：\n{out.strip() or '(无输出)'}", file=sys.stderr, flush=True)

    # 无进展检测：与上一次失败输出完全相同时累计 streak，供路由提前熔断（避免空转到迭代耗尽）
    if test_result.startswith("FAIL") and test_result == state.get("test_result", ""):
        streak = state.get("same_fail_streak", 0) + 1
    elif test_result.startswith("FAIL"):
        streak = 1
    else:
        streak = 0
    # 测试跑过即复位 reviewer 否决计数（语义是「连续否决、中间没跑测试的次数」）
    return {
        "test_result": test_result,
        "status": status,
        "review_vetoes": 0,
        "same_fail_streak": streak,
    }
