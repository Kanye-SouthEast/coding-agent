"""Shell 执行工具：通过沙箱后端执行命令，返回 (退出码, 输出)。"""

from __future__ import annotations

from pathlib import Path

from coding_agent.tools.sandbox import get_sandbox

TIMEOUT = 30


def run_command(workspace: Path, command: str, timeout: int = TIMEOUT, language: str | None = None) -> tuple[int, str]:
    return get_sandbox(language).run(workspace, command, timeout)
