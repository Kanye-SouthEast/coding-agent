"""Docker 沙箱集成测试：验证 DockerSandbox 的隔离语义（执行 / 挂载 / 断网 / 超时 / 退出码）。

默认跳过：需显式设 `RUN_DOCKER_TESTS=1` 且本机 Docker 可用才运行（CI 的 test-docker job 会跑），
避免破坏「pytest 离线、无需 Docker」的默认承诺。

本地跑：
    RUN_DOCKER_TESTS=1 python -m pytest tests/test_docker_sandbox.py -v
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from coding_agent.tools.sandbox import DockerSandbox

IMAGE = "python:3.12-slim"


def _docker_ready() -> bool:
    if os.environ.get("RUN_DOCKER_TESTS") != "1":
        return False
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _docker_ready(), reason="需 RUN_DOCKER_TESTS=1 且 Docker 可用"),
]


def test_run_simple_command(tmp_path):
    sandbox = DockerSandbox(image=IMAGE)
    code, out = sandbox.run(tmp_path, "echo hello-from-container")
    assert code == 0
    assert "hello-from-container" in out


def test_workspace_mount_read(tmp_path):
    (tmp_path / "in.txt").write_text("host-data", encoding="utf-8")
    sandbox = DockerSandbox(image=IMAGE)
    code, out = sandbox.run(tmp_path, "python -c \"print(open('in.txt').read())\"")
    assert code == 0
    assert "host-data" in out


def test_workspace_mount_write(tmp_path):
    sandbox = DockerSandbox(image=IMAGE)
    code, _ = sandbox.run(tmp_path, "python -c \"open('out.txt','w').write('container-data')\"")
    assert code == 0
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "container-data"


def test_network_isolation(tmp_path):
    # --network none 下任何外联都应失败（退出码非 0）
    sandbox = DockerSandbox(image=IMAGE)
    code, _ = sandbox.run(
        tmp_path,
        "python -c \"import socket; socket.create_connection(('8.8.8.8', 53), timeout=3)\"",
        timeout=15,
    )
    assert code != 0


def test_nonzero_exit_code(tmp_path):
    sandbox = DockerSandbox(image=IMAGE)
    code, _ = sandbox.run(tmp_path, "exit 7")
    assert code == 7


def test_timeout(tmp_path):
    sandbox = DockerSandbox(image=IMAGE)
    code, out = sandbox.run(tmp_path, "sleep 60", timeout=2)
    assert code == 124
    assert "timeout" in out
