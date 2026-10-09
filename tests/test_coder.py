"""coder 的 Python 语法自修复门：生成坏代码时用 compile() 检查并回灌错误重试。"""

from __future__ import annotations

from coding_agent.config import settings


def test_python_syntax_errors_flags_broken_code():
    import coding_agent.agents.coder as coder

    bad = "def f(:\n    pass\n"
    good = "def f():\n    pass\n"
    errs = coder._python_syntax_errors({"a.py": bad, "b.py": good, "c.txt": bad})
    assert "a.py" in errs
    assert "b.py" not in errs
    assert "c.txt" not in errs
    assert coder._python_syntax_errors({"ok.py": good}) == ""


def test_coder_self_repairs_on_syntax_error(monkeypatch, tmp_path):
    import coding_agent.agents.coder as coder

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "workspace", tmp_path)
    monkeypatch.setattr(coder, "retrieve_context", lambda q: "")
    monkeypatch.setattr(coder, "index_files", lambda files: len(files))

    calls = {"n": 0}

    def fake_invoke(prompt, model=None, provider=None, timeout=None):
        calls["n"] += 1
        code = "def fib(:\n    return 1\n" if calls["n"] == 1 else "def fib(n):\n    return n\n"
        return {"files": [{"path": "task1.py", "code": code}]}

    monkeypatch.setattr(coder, "invoke_json", fake_invoke)

    state = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "fib", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
        "review_feedback": "",
        "test_result": "",
        "iteration": 0,
    }
    out = coder.coder_node(state)
    assert calls["n"] == 2  # 第一次语法错，触发自修复重试
    assert "def fib(n):" in out["files"]["task1.py"]


def test_coder_removes_stale_files_on_rename(monkeypatch, tmp_path):
    """coder 在同任务内改名模块时，删除上一次写过的孤儿文件（tokenize.py→tokenizer.py）。"""
    import coding_agent.agents.coder as coder

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "workspace", tmp_path)
    monkeypatch.setattr(coder, "retrieve_context", lambda q: "")
    monkeypatch.setattr(coder, "index_files", lambda files: len(files))

    results = [
        {"files": [{"path": "tokenize.py", "code": "def tokenize(s):\n    return []\n"}]},
        {"files": [{"path": "tokenizer.py", "code": "def tokenize(s):\n    return []\n"}]},
    ]
    monkeypatch.setattr(coder, "invoke_json", lambda prompt, model=None, provider=None, timeout=None: results.pop(0))

    base = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "t", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
        "review_feedback": "",
        "test_result": "",
        "iteration": 0,
    }
    out1 = coder.coder_node(base)
    assert (tmp_path / "tokenize.py").exists()
    assert out1["task_files"] == {"0": ["tokenize.py"]}

    out2 = coder.coder_node({**base, "files": out1["files"], "task_files": out1["task_files"], "iteration": 1})
    assert not (tmp_path / "tokenize.py").exists()  # 改名后的孤儿文件被删除
    assert (tmp_path / "tokenizer.py").exists()
    assert list(out2["files"].keys()) == ["tokenizer.py"]
    assert out2["task_files"] == {"0": ["tokenizer.py"]}
