"""state.py 的 format_task / format_files：把任务与代码渲染成可读文本，兼容新旧任务字段。"""

from __future__ import annotations

from coding_agent.state import files_for_task, format_files, format_task


def test_format_task_renders_signature_and_contract():
    task = {
        "id": 1,
        "desc": "two sum",
        "signature": "def two_sum(nums, target):",
        "contract": "result dedup",
        "deps": [0],
        "status": "pending",
    }
    out = format_task(task)
    assert "two sum" in out
    assert "def two_sum(nums, target):" in out
    assert "result dedup" in out
    assert "[0]" in out  # 依赖被渲染


def test_format_task_tolerates_legacy_fields():
    # mock / 降级单任务没有 signature/contract，不能 KeyError
    out = format_task({"id": 1, "desc": "fib", "deps": [], "status": "pending"})
    assert out == "- 描述：fib"


def test_format_files_renders_fenced_blocks():
    files = {"a.py": "def f():\n    return 1\n", "b.py": "x = 1\n"}
    out = format_files(files)
    # 用围栏列出每个文件，而非 repr(dict) 的 \n 转义噪声
    assert "### a.py" in out
    assert "def f():" in out
    assert "### b.py" in out
    assert "x = 1" in out


def test_format_files_empty_placeholder():
    assert format_files({}) == "（暂无）"


# ---- files_for_task：只注入当前任务 + 传递依赖的文件，其余剪掉 ----

def test_files_for_task_prunes_unrelated_tasks():
    """当前任务只依赖 task 1，不依赖 task 2 → 只注入 task 1 的文件，剪掉 task 2 的。"""
    state = {
        "tasks": [
            {"id": 1, "deps": [], "status": "done"},
            {"id": 2, "deps": [], "status": "done"},
            {"id": 3, "deps": [1], "status": "pending"},
        ],
        "current_task_index": 2,
        "files": {"a.py": "a", "b.py": "b"},
        "task_files": {"0": ["a.py"], "1": ["b.py"]},
    }
    assert files_for_task(state) == {"a.py": "a"}


def test_files_for_task_transitive_closure():
    """线性链 1→2→3：当前任务 3 的传递依赖包含 1 和 2 → 注入全部文件。"""
    state = {
        "tasks": [
            {"id": 1, "deps": [], "status": "done"},
            {"id": 2, "deps": [1], "status": "done"},
            {"id": 3, "deps": [2], "status": "pending"},
        ],
        "current_task_index": 2,
        "files": {"a.py": "a", "b.py": "b"},
        "task_files": {"0": ["a.py"], "1": ["b.py"]},
    }
    assert files_for_task(state) == {"a.py": "a", "b.py": "b"}


def test_files_for_task_falls_back_when_no_task_files():
    """老数据（无 task_files 记录）→ 回退全量 files，保持旧行为。"""
    state = {
        "tasks": [{"id": 1, "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {"a.py": "a"},
    }
    assert files_for_task(state) == {"a.py": "a"}


def test_files_for_task_falls_back_when_closure_empty():
    """无依赖首任务、首次编码（闭包内无文件）但有历史文件 → 宁多勿少，回退全量。"""
    state = {
        "tasks": [
            {"id": 1, "deps": [], "status": "done"},
            {"id": 2, "deps": [], "status": "pending"},
        ],
        "current_task_index": 1,
        "files": {"a.py": "a"},
        "task_files": {"0": ["a.py"]},
    }
    assert files_for_task(state) == {"a.py": "a"}


def test_files_for_task_ignores_dangling_dep_ids():
    """deps 引用了不在 tasks 里的 id（如入口任务 deps [1,2] 但列表只有 id 3）→ 不崩溃、不误注入。"""
    state = {
        "tasks": [{"id": 3, "deps": [1, 2], "status": "pending"}],
        "current_task_index": 0,
        "files": {"main.py": "m"},
        "task_files": {"0": ["main.py"]},
    }
    assert files_for_task(state) == {"main.py": "m"}
