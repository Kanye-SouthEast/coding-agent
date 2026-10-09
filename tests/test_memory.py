"""检索排序 / 分块：AST 分块、非 Python 回退、重排序按分数重排、失败回退向量序。"""

from __future__ import annotations

import requests

from coding_agent.memory import _chunk, _chunk_python, _rerank


def test_chunk_python_splits_top_level_defs():
    code = (
        '"""doc"""\n\nimport os\n\n\ndef foo():\n    return 1\n\n\ndef bar():\n    return 2\n\n\nclass A:\n    pass\n'
    )
    chunks = _chunk_python(code)
    assert len(chunks) == 4  # header(docstring+import) + foo + bar + A
    assert any("import os" in c for c in chunks)
    assert any("def foo" in c for c in chunks)
    assert any("def bar" in c for c in chunks)
    assert any("class A" in c for c in chunks)


def test_chunk_python_includes_decorators():
    chunks = _chunk_python("@dec\ndef foo():\n    pass\n")
    assert chunks == ["@dec\ndef foo():\n    pass"]


def test_chunk_python_syntax_error_returns_empty():
    assert _chunk_python("if True print(1)") == []


def test_chunk_fallback_for_non_python():
    js = "function foo() {\n  return 1;\n}\n" * 300
    assert _chunk_python(js) == []
    assert len(_chunk(js, size=100)) > 1


def test_rerank_reorders_by_score(monkeypatch):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    docs = [("a.py", "aaa"), ("b.py", "bbb"), ("c.py", "ccc")]

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "results": [
                    {"index": 2, "relevance_score": 0.99},
                    {"index": 0, "relevance_score": 0.5},
                    {"index": 1, "relevance_score": 0.1},
                ]
            }

    def fake_post(url, json, headers, timeout):
        return FakeResp()

    monkeypatch.setattr(requests, "post", fake_post)
    assert _rerank("q", docs, top_n=2) == [docs[2], docs[0]]


def test_rerank_fallback_keeps_vector_order(monkeypatch):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    docs = [("a.py", "aaa"), ("b.py", "bbb")]

    def fake_post(url, json, headers, timeout):
        raise RuntimeError("boom")

    monkeypatch.setattr(requests, "post", fake_post)
    assert _rerank("q", docs, top_n=2) == docs
