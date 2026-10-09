"""沙箱执行后端：在隔离环境里执行 LLM 生成的不可信代码。

- SubprocessSandbox：本地子进程（不做隔离，仅适合可信 / 演示场景）
- DockerSandbox：Docker 容器，限制内存 / CPU / 网络，挂载工作目录

生产环境执行不可信代码请使用 DockerSandbox。
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

from coding_agent.config import settings
from coding_agent.runtimes import get_runtime


def _fmt(proc: subprocess.CompletedProcess) -> str:
    parts = []
    if proc.stdout.strip():
        parts.append(proc.stdout.strip())
    if proc.stderr.strip():
        parts.append("[stderr] " + proc.stderr.strip())
    return "\n".join(parts)


class SubprocessSandbox:
    """在本地进程执行（无隔离）。"""

    def run(self, workspace: Path, command: str, timeout: int = 30) -> tuple[int, str]:
        workspace.mkdir(parents=True, exist_ok=True)
        # 把当前解释器所在目录前置到 PATH，确保命令里的裸 `python` 解析到
        # 正在运行的 Python，而不是 Windows 商店的占位 python.exe（后者无法执行）。
        env = os.environ.copy()
        bin_dir = os.path.dirname(sys.executable)
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return 124, f"[timeout] 超过 {timeout}s 未结束"
        return proc.returncode, _fmt(proc)


class DockerSandbox:
    """在 Docker 容器里执行，隔离内存 / CPU / 网络。"""

    def __init__(self, image: str = "python:3.12-slim", memory: str = "512m", cpus: str = "1.0"):
        self.image = image
        self.memory = memory
        self.cpus = cpus

    def run(self, workspace: Path, command: str, timeout: int = 30) -> tuple[int, str]:
        workspace.mkdir(parents=True, exist_ok=True)
        host_path = str(workspace.resolve()).replace("\\", "/")
        name = f"coding-agent-{uuid.uuid4().hex[:8]}"

        # 用列表传参（不经过宿主 shell），command 作为单个参数交给容器内 sh -c，
        # 从而保留命令里原有的引号。
        docker_cmd = [
            "docker", "run", "--rm", "--name", name,
            "--network", "none",                 # 断网，防止不可信代码外联
            "--memory", self.memory,
            "--cpus", self.cpus,
            "-v", f"{host_path}:/workspace",
            "-w", "/workspace",
            self.image,
            "sh", "-c", command,
        ]
        try:
            proc = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            # 清理可能残留的容器
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            return 124, f"[timeout] 超过 {timeout}s 未结束"
        return proc.returncode, _fmt(proc)


def get_sandbox(language: str | None = None):
    """按配置返回沙箱后端；docker 后端按语言选择镜像。"""
    if settings.sandbox == "docker":
        image = get_runtime(language or settings.language)["image"]
        return DockerSandbox(image=image)
    return SubprocessSandbox()
