"""命令行入口：跑通一次 规划 → 调度 → 编码 → 审查 → 测试 的完整流程。

状态默认持久化到 SQLite checkpoint（`CODING_AGENT_CHECKPOINT_URI`），
同一 thread_id 可断点续跑 / 事后用 `--inspect` 查看最新状态、`--history` 查看每一轮审查与测试记录。
规划后默认暂停，人工批准计划再执行（--auto-approve 跳过；--resume 从暂停点续跑）。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time

from dotenv import load_dotenv

from coding_agent.config import settings
from coding_agent.graph import build_graph
from coding_agent.metrics import emit_run_metrics
from coding_agent.runtimes import normalize
from coding_agent.state import AgentState


def make_initial_state(requirement: str) -> AgentState:
    return {
        "messages": [],
        "requirement": requirement,
        "tasks": [],
        "current_task_index": -1,
        "files": {},
        "review_feedback": "",
        "review_vetoes": 0,
        "test_result": "",
        "same_fail_streak": 0,
        "task_files": {},
        "iteration": 0,
        "status": "planning",
    }


def derive_thread_id(requirement: str, language: str = "") -> str:
    """用「需求+语言」生成稳定 thread_id：同一需求跨多次运行复用，不同语言用不同会话。"""
    return hashlib.sha256(f"{requirement}\x00{language}".encode("utf-8")).hexdigest()[:16]


def _load_requirement(args) -> str:
    """优先从 UTF-8 文件读取需求，绕开 Windows 命令行中文乱码；否则用命令行参数。

    中文需求在部分终端/IDE 启动器里会因码页不一致被转成乱码（已确认 Windows 下
    CPython 的 sys.argv 本身用宽字符接口、正常情况是正确的，乱码来自启动环境）。
    提供一个 UTF-8 文件的确定性输入通道，一劳永逸。
    """
    if getattr(args, "requirement_file", None):
        with open(args.requirement_file, encoding="utf-8") as f:
            return f.read().strip()
    return args.requirement


def _load_sqlite_saver():
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "持久化 checkpoint 需安装 langgraph-checkpoint-sqlite："
            "pip install langgraph-checkpoint-sqlite"
        ) from e
    return SqliteSaver


def _print_plan(values: dict) -> None:
    print("已规划任务：")
    for t in values.get("tasks") or []:
        deps = ",".join(map(str, t.get("deps") or [])) or "-"
        print(f"  [{t.get('id')}] {t.get('desc')}  (依赖: {deps})")


def _print_state(values: dict, indent: str = "  ") -> None:
    print(f"{indent}需求: {values.get('requirement', '')}")
    print(f"{indent}状态: {values.get('status', '')}")
    tasks = values.get("tasks") or []
    for t in tasks:
        print(f"{indent}  [{t.get('id')}] {t.get('desc')} -> {t.get('status')}")
    files = values.get("files") or {}
    print(f"{indent}文件: {', '.join(files) or '(无)'}")
    review = (values.get("review_feedback") or "").strip()
    if review:
        print(f"{indent}审查意见: {review}")
    test_result = (values.get("test_result") or "").strip()
    if test_result:
        print(f"{indent}测试结果: {test_result}")


def _print_summary(result: dict, thread_id: str) -> None:
    print("\n" + "=" * 60)
    print(f"模式: {'mock（演示，无需 API key）' if settings.mock else '真实 LLM'}")
    if not settings.mock:
        print(f"语言: {settings.language}")
    print(f"thread_id: {thread_id}")
    print(f"checkpoint 文件: {settings.checkpoint_uri}")
    _print_state(result)
    print("=" * 60)


def _report_failure(e: Exception) -> None:
    """节点抛异常时给出友好提示而非裸 traceback；checkpoint 已持久化，可 --resume 续跑。"""
    name = type(e).__name__
    print("\n" + "=" * 60, file=sys.stderr)
    print(f"运行中断：{name}: {e}", file=sys.stderr)
    if "Timeout" in name or "Connect" in name:
        print("提示：上游模型请求超时/网络抖动，属瞬时问题。checkpoint 已保存，稍后可用 --resume 从断点续跑。", file=sys.stderr)
    else:
        print("提示：checkpoint 已保存，可用 --resume 续跑；如需排查请查看上方异常原因。", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    sys.exit(1)


def _make_config(thread_id: str) -> dict:
    """构造 invoke 的 config：附上 LangSmith 元数据（language/provider/mode/thread_id），
    便于在 LangSmith 里按语言、供应商、运行模式过滤与聚合 trace。"""
    return {
        "configurable": {"thread_id": thread_id},
        "metadata": {
            "language": settings.language,
            "provider": settings.provider,
            "run_mode": "mock" if settings.mock else "real",
            "thread_id": thread_id,
        },
    }


def run(args, thread_id: str) -> None:
    SqliteSaver = _load_sqlite_saver()
    start = time.monotonic()
    with SqliteSaver.from_conn_string(settings.checkpoint_uri) as checkpointer:
        app = build_graph(checkpointer)
        config = _make_config(thread_id)
        try:
            result = app.invoke(make_initial_state(args.requirement), config=config)

            # 规划后暂停，等待人工批准
            if settings.interrupt_before_plan:
                snap = app.get_state(config)
                if snap.next and "approve" in snap.next:
                    _print_plan(snap.values)
                    ans = input("\n批准该计划并继续执行? [y/N] ").strip().lower()
                    if ans in ("y", "yes"):
                        result = app.invoke(None, config=config)
                    else:
                        print("已中止。计划已持久化，可稍后 --resume 继续。")
                        return
        except KeyboardInterrupt:
            print("\n已手动中断。checkpoint 已保存，可 --resume 续跑。")
            sys.exit(130)
        except Exception as e:
            _report_failure(e)

    _print_summary(result, thread_id)
    emit_run_metrics(result, thread_id, time.monotonic() - start)


def resume(args, thread_id: str) -> None:
    SqliteSaver = _load_sqlite_saver()
    start = time.monotonic()
    with SqliteSaver.from_conn_string(settings.checkpoint_uri) as checkpointer:
        app = build_graph(checkpointer)
        config = _make_config(thread_id)
        snap = app.get_state(config)
        if not snap.next:
            print("该 thread 没有待续跑的步骤（未运行或已结束），可用 --inspect 查看状态")
            return
        _print_plan(snap.values)
        try:
            result = app.invoke(None, config=config)
        except KeyboardInterrupt:
            print("\n已手动中断。checkpoint 已保存，可 --resume 续跑。")
            sys.exit(130)
        except Exception as e:
            _report_failure(e)
    _print_summary(result, thread_id)
    emit_run_metrics(result, thread_id, time.monotonic() - start)


def inspect(args, thread_id: str) -> None:
    SqliteSaver = _load_sqlite_saver()
    with SqliteSaver.from_conn_string(settings.checkpoint_uri) as checkpointer:
        app = build_graph(checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        try:
            history = list(app.get_state_history(config))
        except Exception:  # 无该 thread 或数据库为空
            history = []

    print("\n" + "=" * 60)
    print(f"thread_id: {thread_id}")
    print(f"checkpoint 文件: {settings.checkpoint_uri}")
    if not history:
        print("未找到该 thread_id 的 checkpoint（可先运行一次，或用 --thread-id 指定）")
    else:
        print(f"checkpoint 数: {len(history)}")
        print("最新状态:")
        _print_state(history[0].values)
    print("=" * 60)


def show_history(args, thread_id: str) -> None:
    """列出该 thread 每一轮审查意见 / 测试结果的时间线（--history）。"""
    SqliteSaver = _load_sqlite_saver()
    with SqliteSaver.from_conn_string(settings.checkpoint_uri) as checkpointer:
        app = build_graph(checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        try:
            snapshots = list(app.get_state_history(config))
        except Exception:  # 无该 thread 或数据库为空
            snapshots = []

    print("\n" + "=" * 60)
    print(f"thread_id: {thread_id}")
    print(f"checkpoint 文件: {settings.checkpoint_uri}")
    if not snapshots:
        print("未找到该 thread_id 的 checkpoint（可先运行一次，或用 --thread-id 指定）")
        print("=" * 60)
        return

    # get_state_history 最新在前，反转为时间正序
    snapshots.reverse()
    print(f"checkpoint 数: {len(snapshots)}")
    print("各轮审查 / 测试记录（时间正序）：")

    def _task_desc(v: dict) -> str:
        tasks = v.get("tasks") or []
        idx = v.get("current_task_index", -1)
        if 0 <= idx < len(tasks):
            t = tasks[idx]
            return f"任务[{t.get('id')}] {t.get('desc')}"
        return ""

    shown = 0
    for snap in snapshots:
        v = snap.values
        desc = _task_desc(v)
        if not desc:
            continue  # 无当前任务的终态快照（如流程结束），跳过
        it = v.get("iteration", 0)
        review = (v.get("review_feedback") or "").strip()
        test_result = (v.get("test_result") or "").strip()
        status = v.get("status", "")
        # status 唯一区分决策节点：reviewer 返回 testing，tester 失败 coding / 通过 done
        if status == "testing" and review:
            print(f"  第 {it} 轮 {desc} 审查未通过：{review}")
            shown += 1
        elif status == "coding" and test_result.startswith("FAIL"):
            print(f"  第 {it} 轮 {desc} 测试未通过：{test_result}")
            shown += 1
        elif status == "done" and test_result.startswith("PASS"):
            print(f"  第 {it} 轮 {desc} 测试通过")
            shown += 1

    if shown == 0:
        print("  (无审查/测试记录)")
    print("-" * 60)
    print("最终状态：")
    _print_state(snapshots[-1].values)
    print("=" * 60)


def main() -> None:
    load_dotenv()
    # Windows 控制台默认 GBK，强制 UTF-8 输出，避免中文乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:  # 旧版 Python 无 reconfigure
            pass

    parser = argparse.ArgumentParser(description="自主编程多 Agent 框架")
    parser.add_argument(
        "requirement",
        nargs="?",
        default="写一个计算斐波那契数列的函数",
        help="要完成的需求（默认演示斐波那契）",
    )
    parser.add_argument("--mock", dest="mock", action="store_true", default=settings.mock)
    parser.add_argument("--no-mock", dest="mock", action="store_false", help="使用真实 LLM")
    parser.add_argument(
        "--language",
        default=settings.language,
        help="实现语言（python | javascript | cpp | java | go | rust），决定沙箱镜像与测试运行方式",
    )
    parser.add_argument(
        "--thread-id",
        dest="thread_id",
        default=None,
        help="会话 ID（默认由需求自动生成，同一需求可续跑/查看）",
    )
    parser.add_argument(
        "--requirement-file",
        "-f",
        dest="requirement_file",
        default=None,
        help="从 UTF-8 文件读取需求（避开中文命令行乱码），优先级高于位置参数",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="不运行，只查看该 thread_id 已持久化的最新状态",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="不运行，只列出该 thread_id 每一轮的审查意见与测试结果时间线",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="从该 thread_id 的中断点续跑（配合计划审批）",
    )
    parser.add_argument(
        "--auto-approve",
        "-y",
        action="store_true",
        help="自动批准计划，规划后不暂停",
    )
    args = parser.parse_args()

    # 需求优先走 UTF-8 文件（--requirement-file），避免中文命令行乱码
    args.requirement = _load_requirement(args)

    settings.mock = args.mock
    try:
        settings.language = normalize(args.language)
    except ValueError as e:
        parser.error(str(e))
    settings.workspace.mkdir(parents=True, exist_ok=True)
    if args.auto_approve:
        settings.interrupt_before_plan = False

    thread_id = args.thread_id or derive_thread_id(args.requirement, settings.language)

    if args.history:
        show_history(args, thread_id)
    elif args.inspect:
        inspect(args, thread_id)
    elif args.resume:
        resume(args, thread_id)
    else:
        run(args, thread_id)


if __name__ == "__main__":
    main()
