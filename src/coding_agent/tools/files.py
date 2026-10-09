"""文件工具：在工作区内读写文件（限制路径越界）。"""

from __future__ import annotations

from pathlib import Path


def _resolve(workspace: Path, path: str) -> Path:
    root = workspace.resolve()
    full = (root / path).resolve()
    # 防止通过 ../ 或绝对路径逃逸到工作区之外；is_relative_to 避免 sibling 前缀误判
    if not full.is_relative_to(root):
        raise ValueError(f"拒绝访问工作区之外: {path}")
    return full


def write_file(workspace: Path, path: str, content: str) -> str:
    full = _resolve(workspace, path)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return f"已写入 {path}（{len(content)} 字符）"


def read_file(workspace: Path, path: str) -> str:
    return _resolve(workspace, path).read_text(encoding="utf-8")
