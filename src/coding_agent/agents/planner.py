"""规划 Agent：把需求拆解成带依赖关系的任务 DAG。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from coding_agent.config import settings
from coding_agent.llm import invoke_json
from coding_agent.runtimes import get_runtime
from coding_agent.state import AgentState
from coding_agent.memory import index_plan, search_plans

PLAN_PROMPT = """你是资深软件架构师。把下面的需求拆解成带依赖关系的任务列表，并为每个任务给出**精确的接口契约**，让下游 coder 能直接照着实现、不必猜测接口。

需求：
{requirement}

约束：
- 所有任务都实现为 {language} 代码。
- 每个任务实现一个具体的可复用函数/类（如「实现 twoSum 函数」），不要写 main()/入口函数、不要写测试代码、不要拆出纯分析/设计/文档类任务。例外：若需求明确要求一个可运行的入口（如命令行工具/CLI），允许最后一个任务额外生成 main()/入口，但入口只负责解析参数并调用前面任务的函数。
- 任务总数严格控制在 1~5 个任务，复杂需求也要适度合并，禁止生成大量细碎任务。
- signature 要精确到可直接实现：写清函数/类名、参数名与类型、返回类型，类型注解语法必须匹配 {language}。被依赖任务的 signature 是下游 import/调用它的唯一依据，后续任务调用必须严格复用该签名，禁止私自修改参数。
- 禁止循环依赖，deps 不能构成环；id 必须从 1 开始连续正整数。
- contract 字段为字符串，内部用序号逐条写清单，完整定义：合法输入、非法输入、输入对应的输出行为、非法输入下的处理方式（抛异常/返回值）、本任务不负责的边界。逐条做到：
  1) 明确区分：本任务负责什么，**本任务不负责什么**，避免 tester 越界断言其他任务职责。
  2) 必须写明非法输入时函数的行为：抛出什么异常，或是返回什么特定值，不得省略。
  3) 规则必须自洽，不能出现互相矛盾的合法/非法定义。没有特殊边界才写「无」。
  4) invalid_inputs 必须与 contract 的「非法输入」条目一一对应、完整覆盖：contract 里写了的非法输入都要列进 invalid_inputs，没写的不得擅自加入；本任务不负责的边界（如词法任务里的语法/求值错误）不得列入。
{scenario_hint}

输出硬性格式规则：
仅输出纯净 JSON 数组，**禁止输出任何 markdown 代码块、禁止输出解释、注释、前置或后置文字**。输出内容必须可以被 JSON 解析器直接加载成功。

JSON 数组每个元素字段：
- id: 整数，任务编号，从 1 开始连续递增
- desc: 一句话任务描述
- signature: 本任务要实现的精确签名
- contract: 字符串，结构化逐条写明合法输入、非法输入、异常行为、职责边界
- invalid_inputs: 字符串数组，本任务 contract 里「非法输入」的逐条清单（只列本任务这一层必须拒绝的输入，尽量用具体输入示例，一条一个；没有非法输入则为空数组 []）。必须与 contract 的非法输入条目一一对应、不得遗漏，也不得把其他层（词法/解析/求值）的职责算进来。
- deps: 依赖的前置任务 id 数组，无依赖为空数组 []
- status: 固定为 pending

示例：
[
    {{
        "id": 1,
        "desc": "实现两数之和",
        "signature": "def two_sum(nums: list[int], target: int) -> list[list[int]]",
        "contract": "1.合法输入：nums 可为空列表，可包含重复整数；target 为整数。2.非法输入：无。3.输出：返回全部和等于 target 的下标二元组列表，结果去重；无解返回空列表。4.本任务不做输入参数类型校验。",
        "invalid_inputs": [],
        "deps": [],
        "status": "pending"
    }},
    {{
        "id": 2,
        "desc": "实现两数之和的布尔判断",
        "signature": "def has_pair(nums: list[int], target: int) -> bool",
        "contract": "1.合法输入：nums 可为空列表，target 为整数。2.非法输入：无。3.输出：调用 two_sum，存在解返回 True，不存在返回 False。4.本任务不做参数类型校验。",
        "invalid_inputs": [],
        "deps": [1],
        "status": "pending"
    }}
]
{fewshot}

不要输出任何其他文字。
"""


# ---- 场景片段：按需求关键词注入针对性的 contract / 拆解提示 ----
# 通用骨架只写「放之四海皆准」的规则；不同任务类型的专项约束（解析器的运算符/分层、
# 算法的边界/去重）放在这里，由 _select_scenario 按关键词选中注入，避免通用 prompt 里
# 塞满与当前任务无关的噪音。新增场景只需在 SCENARIOS 里加一条（含 name/keywords/hint）。
SCENARIOS: dict[str, dict] = {
    "parser": {
        "name": "解析器/编译器",
        "keywords": [
            "计算器", "calculator", "解析", "parse", "编译", "compiler", "词法", "语法",
            "表达式", "expression", "求值", "evaluate", "token", "ast", "dsl",
            "解释器", "interpreter", "正则", "四则",
        ],
        "hint": (
            "  【本需求属于「解析器/编译器」类，contract 额外注意：】\n"
            "  - 运算符规则写死到无歧义：一元负号 - 合法于「表达式开头 -1、左括号后 (-2)、任意二元运算符后 1*-2」；一元正号 + 一律非法（+1 非法）；连续两个运算符仅当第二个是作一元负号的 - 时合法（1*-2 合法），其余 1++2、1**2 一律非法。\n"
            "  - 数字格式（1.2.3 / .5 / 5. / 1..2）、括号匹配、特殊字符规则逐条列明合法/非法。\n"
            "  - 分清层级：词法任务只分词、不判语法/语义（运算符连续、数字相邻、括号不匹配、除零都归后续解析/求值任务）；解析/求值任务才负责这些。\n"
            "  - 通常按「词法 → 语法解析 → 求值」分层拆任务，各层契约只写自己那层。"
        ),
    },
    "algorithm": {
        "name": "算法/数据结构",
        "keywords": [
            "算法", "algorithm", "排序", "sort", "搜索", "search", "两数", "two sum",
            "合并", "merge", "动态规划", "dp", "图", "graph", "树", "tree", "栈", "stack",
            "队列", "queue", "哈希", "hash", "链表", "linked list", "二分", "binary",
            "leetcode", "数据结构", "去重", "前缀和", "回溯", "滑动窗口",
        ],
        "hint": (
            "  【本需求属于「算法/数据结构」类，contract 额外注意：】\n"
            "  - 输入范围与边界逐条写清：空输入、单元素、重复元素、含 0 / 负数 / 极大值等。\n"
            "  - 输出约束写清：是否去重、是否保持顺序、返回格式（列表/元组/字典）、无解时的返回值（空列表 / -1 / None 等）。\n"
            "  - 结果唯一性/稳定性写清：多解时返回全部 / 任意一个 / 字典序最小。\n"
            "  - 通常 1~2 个任务即可，别为凑数拆碎（如「判断是否存在」可依赖「求所有解」）。\n"
            "  - 若需求要求可运行入口：算法函数放独立模块（如 solution.py），入口 main() 单独放 main.py 并 import 算法函数，避免多个任务写同一文件互相覆盖。"
        ),
    },
    "generic": {
        "name": "通用",
        "keywords": [],
        "hint": "",
    },
}


def _select_scenario(requirement: str) -> dict:
    """按需求文本里的关键词选择场景片段；匹配不到则回退到通用场景。"""
    text = (requirement or "").lower()
    for name, sc in SCENARIOS.items():
        if name == "generic":
            continue
        if any(kw.lower() in text for kw in sc["keywords"]):
            return sc
    return SCENARIOS["generic"]


# mock 演示用：3 个任务线性依赖，任务 2/3 分别 import 前一个任务的产出
MOCK_TASKS = [
    {"id": 1, "desc": "实现 fib 函数", "deps": [], "status": "pending"},
    {"id": 2, "desc": "实现 fib_list（前 n 项列表）", "deps": [1], "status": "pending"},
    {"id": 3, "desc": "实现 fib_sum（前 n 项求和）", "deps": [2], "status": "pending"},
]


def _fallback_tasks(requirement: str) -> list[dict]:
    """规划失败时的兜底：把整条需求当作一个无依赖的任务，让流程继续。"""
    return [{"id": 1, "desc": requirement, "deps": [], "status": "pending"}]


# ---- 计划缓存：同一需求复用上次拆解，保证 planner 稳定 ----

def _plan_cache_key(prompt: str, model: str, provider: str) -> str:
    """计划缓存键：由 prompt（含需求+语言+PLAN_PROMPT 全文）+ 模型 + 供应商共同决定。

    只要 PLAN_PROMPT、需求、语言、模型任一变化，键就变，旧缓存自动失效。
    """
    raw = f"{model}\x00{provider}\x00{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_plan_cache() -> dict:
    path = Path(settings.planner_cache_file)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_plan_cache(cache: dict) -> None:
    path = Path(settings.planner_cache_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _format_fewshot(pairs: list[dict]) -> str:
    """把相近历史计划格式化成 prompt 里的参考块（仅作拆分粒度/契约写法参考，明确禁止照抄）。"""
    lines = [
        "  【参考】历史上与本题意相近的需求及其拆解（仅作拆分粒度、契约写法参考，**不要照抄**——"
        "相近不等于相同，签名/边界/任务数都应以当前需求为准）："
    ]
    for p in pairs:
        req = p.get("requirement", "")
        plan = p.get("plan", [])
        if not req or not plan:
            continue
        lines.append(f"  相近需求：{req}")
        lines.append("  它的拆解：" + json.dumps(plan, ensure_ascii=False))
    return "\n".join(lines)


def planner_node(state: AgentState) -> dict:
    requirement = state["requirement"]
    print(f"[planner] 拆解需求：{requirement}", flush=True)
    if settings.mock:
        tasks = [dict(t) for t in MOCK_TASKS]
    else:
        scenario = _select_scenario(requirement)
        print(f"[planner] 场景识别：{scenario['name']}", flush=True)
        # 缓存键只看确定性部分（需求+语言+PLAN_PROMPT+模型+供应商），few-shot 不参与：否则
        # 缓存命中后重跑时 prompt 又掺入新的 few-shot，键会变，导致永远命中不了。
        base_prompt = PLAN_PROMPT.format(
            requirement=requirement,
            language=get_runtime(settings.language)["label"],
            scenario_hint=scenario["hint"],
            fewshot="",
        )
        key = _plan_cache_key(
            base_prompt, settings.planner_model or "", settings.planner_provider or ""
        )
        cache = _load_plan_cache()
        if key in cache:
            tasks = [dict(t) for t in cache[key]]
            print("[planner] 命中计划缓存，复用上次拆解（保证同一需求拆解一致）", flush=True)
        else:
            # 缓存未命中：从 Milvus 语义检索相近历史计划，注入为 few-shot 引导这次拆解。
            # 注意这是「引导」而非「复用」——最终计划仍由 planner 针对当前需求生成，避免拿
            # 相近但不相同的契约去复用导致下游 coder/tester 越界断言。
            fewshot_pairs = (
                search_plans(requirement, k=settings.planner_fewshot_k)
                if settings.planner_fewshot_k > 0
                else []
            )
            fewshot = _format_fewshot(fewshot_pairs) if fewshot_pairs else ""
            prompt = (
                PLAN_PROMPT.format(
                    requirement=requirement,
                    language=get_runtime(settings.language)["label"],
                    scenario_hint=scenario["hint"],
                    fewshot=fewshot,
                )
                if fewshot
                else base_prompt
            )
            try:
                data = invoke_json(
                    prompt,
                    model=settings.planner_model or None,
                    provider=settings.planner_provider or None,
                    timeout=settings.planner_timeout or None,
                )
                tasks = data if isinstance(data, list) else data.get("tasks", [])
                if not isinstance(tasks, list) or not tasks:
                    raise ValueError("规划结果为空/非法")
            except Exception as e:
                # 降级：规划失败或结果空/非法时兜底为单个任务，让流程继续（不崩溃、不静默结束）。
                print(
                    f"[planner] 规划失败（降级为单任务）: {type(e).__name__}: {e}",
                    file=sys.stderr,
                    flush=True,
                )
                tasks = _fallback_tasks(requirement)
            else:
                # 仅当 LLM 成功产出非空任务列表时才写缓存；失败/兜底不缓存，下次仍会重试
                cache[key] = tasks
                _save_plan_cache(cache)
                # 把这次 (需求→计划) 写入 Milvus，供后续相近需求做 few-shot；已有完全相同
                # 需求的计划时跳过，避免重复行堆积。
                if settings.planner_fewshot_k > 0 and not any(
                    p.get("requirement") == requirement for p in fewshot_pairs
                ):
                    index_plan(requirement, tasks)
    return {"tasks": tasks, "status": "planning"}
