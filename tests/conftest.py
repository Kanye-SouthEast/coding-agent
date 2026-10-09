"""测试夹具：强制 mock + subprocess + 不中断 + 临时工作区，保证离线、无需 Docker/LLM/Milvus。"""

from __future__ import annotations

import pytest

from coding_agent.config import settings


@pytest.fixture(autouse=True)
def _safe_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "mock", True)
    monkeypatch.setattr(settings, "sandbox", "subprocess")
    monkeypatch.setattr(settings, "interrupt_before_plan", False)
    monkeypatch.setattr(settings, "workspace", tmp_path)
    monkeypatch.setattr(settings, "planner_cache_file", str(tmp_path / "planner_cache.json"))
    yield
