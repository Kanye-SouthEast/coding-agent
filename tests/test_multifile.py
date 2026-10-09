"""多文件输出与编译型语言测试脚手架：coder 一次写多文件、tester 固定测试名 + 项目脚手架。"""

from __future__ import annotations

from coding_agent.config import settings


def _task_state():
    return {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "two sum", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
        "review_feedback": "",
        "test_result": "",
        "iteration": 0,
        "status": "coding",
    }


def test_coder_writes_multiple_files(monkeypatch, tmp_path):
    import coding_agent.agents.coder as coder

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "workspace", tmp_path)
    monkeypatch.setattr(coder, "retrieve_context", lambda q: "")
    monkeypatch.setattr(
        coder,
        "invoke_json",
        lambda prompt, model=None, provider=None, timeout=None: {
            "files": [
                {"path": "two_sum.h", "code": "#pragma once\nint two_sum(int a, int b);\n"},
                {
                    "path": "two_sum.cpp",
                    "code": '#include "two_sum.h"\nint two_sum(int a, int b) { return a + b; }\n',
                },
            ]
        },
    )
    monkeypatch.setattr(coder, "index_files", lambda files: len(files))

    out = coder.coder_node(_task_state())

    assert set(out["files"]) == {"two_sum.h", "two_sum.cpp"}
    assert (tmp_path / "two_sum.h").read_text(encoding="utf-8") == "#pragma once\nint two_sum(int a, int b);\n"


def test_coder_accepts_single_file_fallback(monkeypatch, tmp_path):
    import coding_agent.agents.coder as coder

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "workspace", tmp_path)
    monkeypatch.setattr(coder, "retrieve_context", lambda q: "")
    monkeypatch.setattr(
        coder,
        "invoke_json",
        lambda prompt, model=None, provider=None, timeout=None: {
            "path": "two_sum.py",
            "code": "def two_sum(a, b):\n    return a + b\n",
        },
    )
    monkeypatch.setattr(coder, "index_files", lambda files: len(files))

    out = coder.coder_node(_task_state())

    assert set(out["files"]) == {"two_sum.py"}


def test_tester_uses_fixed_test_file_and_scaffold(monkeypatch, tmp_path):
    import coding_agent.agents.tester as tester

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "rust")
    monkeypatch.setattr(settings, "workspace", tmp_path)
    monkeypatch.setattr(
        tester,
        "invoke_json",
        lambda prompt, model=None, max_tokens=None: {"path": "ignored.rs", "code": "use app::two_sum;\n"},
    )
    ran = {}

    def fake_run(ws, cmd, language=None):
        ran["cmd"] = cmd
        return (0, "ok")

    monkeypatch.setattr(tester, "run_command", fake_run)

    out = tester.tester_node(_task_state())

    assert out["status"] == "done"
    assert ran["cmd"] == "cargo test"
    assert (tmp_path / "tests" / "main_test.rs").read_text(encoding="utf-8") == "use app::two_sum;\n"
    assert (tmp_path / "Cargo.toml").exists()
