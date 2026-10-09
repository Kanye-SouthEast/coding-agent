"""CLI 辅助：thread_id 派生（需求+语言）稳定性与分隔；需求从 UTF-8 文件读取。"""

from __future__ import annotations

from coding_agent.main import derive_thread_id, _load_requirement


def test_derive_thread_id_stable_for_same_input():
    assert derive_thread_id("fib", "python") == derive_thread_id("fib", "python")


def test_derive_thread_id_differs_by_language():
    assert derive_thread_id("fib", "python") != derive_thread_id("fib", "javascript")


def test_derive_thread_id_differs_by_requirement():
    assert derive_thread_id("fib", "python") != derive_thread_id("fac", "python")


def test_load_requirement_from_file(tmp_path):
    f = tmp_path / "req.txt"
    f.write_text("实现一个四则计算器\n", encoding="utf-8")
    args = type("A", (), {"requirement": "fallback", "requirement_file": str(f)})()
    assert _load_requirement(args) == "实现一个四则计算器"


def test_load_requirement_falls_back_to_arg():
    args = type("A", (), {"requirement": "hello", "requirement_file": None})()
    assert _load_requirement(args) == "hello"
