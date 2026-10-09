"""文件工具：路径越界防护（../ 逃逸与 sibling 前缀误判）。"""

from __future__ import annotations

import pytest

from coding_agent.tools.files import write_file


def test_write_file_ok_inside(tmp_path):
    msg = write_file(tmp_path, "sub/a.py", "x = 1")
    assert (tmp_path / "sub" / "a.py").exists()
    assert "a.py" in msg


def test_write_file_rejects_parent_escape(tmp_path):
    with pytest.raises(ValueError):
        write_file(tmp_path, "../evil.py", "x = 1")


def test_write_file_rejects_absolute_path(tmp_path):
    with pytest.raises(ValueError):
        write_file(tmp_path, str(tmp_path.parent / "other.py"), "x = 1")


def test_write_file_rejects_sibling_prefix(tmp_path):
    # 同级目录 <name>_backup 不应被当作 <name> 内部（此前 startswith 前缀匹配会误判）
    sibling = tmp_path.parent / (tmp_path.name + "_backup")
    sibling.mkdir(exist_ok=True)
    with pytest.raises(ValueError):
        write_file(tmp_path, f"../{sibling.name}/x.py", "x = 1")
