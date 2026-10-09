"""从 LLM 输出中稳健提取 JSON 的工具函数。"""

from __future__ import annotations

import json
import re

# JSON 合法的转义字符集合：\" \\ \/ \b \f \n \r \t 以及 \uXXXX 的起始字符 u
_VALID_JSON_ESCAPES = set('"\\/bfnrtu')


def _strip_trailing_commas(text: str) -> str:
    """去掉 `}` / `]` 前多余的尾随逗号（如 `{"a":1,}` / `[1,2,3,]`）。"""
    return re.sub(r",\s*([}\]])", r"\1", text)


def _fix_invalid_escapes(text: str) -> str:
    r"""把字符串里不合法的 JSON 转义（如代码里的正则 `\d`、`\s` 被模型写成了单反斜杠）补成双反斜杠。

    LLM 生成含代码的 JSON 时，最常见错误就是正则/转义序列只写了一个反斜杠（`\d`），
    这在 JSON 里是非法转义，导致 json.loads 直接失败。这里把「后跟非法字符的单反斜杠」
    补成字面量反斜杠，`\d` → `\\d`（解码后仍是 `\d`，正好是代码想要的）。
    对合法 JSON 无副作用：`\\`、`\n`、`\"`、`\uXXXX` 等合法转义一律原样保留。
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "\\":
                out.append("\\\\")  # 已是转义反斜杠 \\，原样保留
                i += 2
            elif nxt in _VALID_JSON_ESCAPES or nxt in "'":
                out.append(ch)  # 合法转义（或引号，避免误伤字符串边界），不动
                i += 1
            else:
                out.append("\\\\")  # 非法转义 → 补成字面量反斜杠
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def extract_json(text: str):
    """从模型输出中提取 JSON 对象或数组，容忍前后有多余文字 / markdown 代码块 / 尾随逗号 / 非法转义。"""
    text = text.strip()
    # 去掉 markdown 代码块围栏
    text = re.sub(r"^```(?:json|JSON)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()

    # 候选片段：整段文本，以及「第一个 { 或 [ 到最后一个 } 或 ]」的切片
    candidates = [text]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])

    for cand in candidates:
        if not cand.strip():
            continue
        # 依次尝试：原文 → 去尾逗号 → 修非法转义 → 两者叠加
        for repaired in (
            cand,
            _strip_trailing_commas(cand),
            _fix_invalid_escapes(cand),
            _fix_invalid_escapes(_strip_trailing_commas(cand)),
        ):
            try:
                return json.loads(repaired, strict=False)
            except json.JSONDecodeError:
                continue

    raise ValueError(f"无法从模型输出中解析 JSON，输出前 200 字符：{text[:200]!r}")
