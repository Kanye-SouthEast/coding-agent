"""JSON 提取：容忍 markdown 围栏与前后多余文字。"""

from __future__ import annotations

import pytest

from coding_agent.jsonx import extract_json


def test_extract_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_plain_array():
    assert extract_json('[{"id": 1}, {"id": 2}]') == [{"id": 1}, {"id": 2}]


def test_extract_strips_markdown_fence():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_ignores_surrounding_text():
    assert extract_json('这是结果：{"a": 1} 结束') == {"a": 1}


def test_extract_raises_on_garbage():
    with pytest.raises(ValueError):
        extract_json("not json at all")


def test_extract_tolerates_trailing_comma():
    assert extract_json('{"a": 1,}') == {"a": 1}
    assert extract_json("[1, 2, 3,]") == [1, 2, 3]


def test_extract_repairs_invalid_regex_escapes():
    # 模型常把代码里的正则 \d \s 写成单反斜杠，JSON 会因非法转义而失败
    raw = '{"code": "re.compile(r\'\\s*(\\d+)\')"}'
    result = extract_json(raw)
    assert result["code"] == r"re.compile(r'\s*(\d+)')"


def test_extract_keeps_valid_escapes_intact():
    # 合法转义 \\ 与 \n 不能被误伤
    assert extract_json('{"a": "\\\\d"}')["a"] == "\\d"
    assert extract_json('{"a": "a\\nb"}')["a"] == "a\nb"
