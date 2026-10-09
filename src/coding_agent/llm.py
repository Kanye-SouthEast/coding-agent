"""统一模型工厂：按配置返回 SiliconFlow / Moonshot / OpenAI / Anthropic 模型。"""

from __future__ import annotations

import os
import threading
import time

from coding_agent.config import settings
from coding_agent.jsonx import extract_json

# OpenAI 兼容协议供应商 -> 对应的 API key 环境变量名
_OPENAI_COMPAT_KEYS = {
    "siliconflow": "SILICONFLOW_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

# 部分供应商的 base_url 环境变量名（缺省回退 settings.base_url）
_BASE_URL_KEYS = {
    "deepseek": "DEEPSEEK_BASE_URL",
}


def get_model(
    model_name: str | None = None,
    provider: str | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
):
    """返回一个 LangChain chat model，temperature=0 保证输出稳定。

    model_name 为空时用 settings.model（主力模型）；各节点可传入自己的模型名覆盖。
    provider 可指定节点专用供应商（如 planner 走 deepseek），为空则用全局 settings.provider。
    max_tokens 可逐节点覆盖（如 tester 输出长测试脚本需要更大预算）；为 None 时回退全局 settings.max_tokens。
    timeout 可逐节点覆盖墙钟超时（如 planner/coder 用推理模型思考久，放宽到 600s）；为 None 时回退全局 settings.llm_timeout。
    显式设置 timeout / max_retries，避免单个挂起的请求阻塞整个流程太久。
    """
    provider = provider or settings.provider
    if timeout is None:
        timeout = settings.llm_timeout
    max_retries = settings.llm_max_retries
    model = model_name or settings.model
    if max_tokens is None:
        max_tokens = settings.max_tokens

    if provider in _OPENAI_COMPAT_KEYS:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(f"使用 {provider} 需安装 langchain-openai：pip install langchain-openai") from e

        kwargs = {
            "model": model,
            "temperature": 0,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if provider != "openai":  # 中转/国产端点需要显式 base_url 和 key
            api_key = os.getenv(_OPENAI_COMPAT_KEYS[provider])
            if not api_key:
                raise RuntimeError(f"缺少 {_OPENAI_COMPAT_KEYS[provider]}，请在 .env 或环境变量中配置")
            kwargs["base_url"] = os.getenv(_BASE_URL_KEYS.get(provider, "")) or settings.base_url
            kwargs["api_key"] = api_key
        return ChatOpenAI(**kwargs)

    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("使用 Anthropic 需安装 langchain-anthropic：pip install langchain-anthropic") from e
    return ChatAnthropic(
        model=model,
        temperature=0,
        timeout=timeout,
        max_retries=max_retries,
    )


def _is_transient(exc: Exception) -> bool:
    """判断是否可重试的瞬时错误（超时 / 连接），按异常类名判断，不依赖具体 SDK。"""
    name = type(exc).__name__
    return "Timeout" in name or "Connect" in name


_TIMEOUT = object()  # 墙钟超时哨兵：与正常返回值区分


def _call_with_timeout(fn, seconds: float, *args, **kwargs):
    """在守护线程里跑 fn，超过 seconds 秒即放弃并返回 _TIMEOUT。

    底层 SDK（httpx）的 timeout 有时拦不住「连接建立后一直不响应」的挂起，
    单靠参数 timeout 不可靠，这里用线程 + 墙钟时间兜底：超时即丢弃线程、主流程快速失败。
    """
    box: dict = {}

    def _run():
        try:
            box["value"] = fn(*args, **kwargs)
        except Exception as exc:  # 异常回传给调用线程，交由上层 try/except 处理
            box["error"] = exc

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        return _TIMEOUT
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _extract_content(result) -> str:
    """取模型的最终答案：优先 content；推理模型 content 可能为空，此时回退 reasoning_content。

    DeepSeek 推理模型（reasoner/v4-pro/v4-flash）经 OpenAI 兼容协议返回时，答案可能落在
    reasoning_content 而非 content；这里兜底兼容两种情况，避免拿到空串导致下游 JSON 解析失败。
    """
    content = getattr(result, "content", None) or ""
    if content:
        return content
    extra = getattr(result, "additional_kwargs", None) or {}
    meta = getattr(result, "response_metadata", None) or {}
    return (
        extra.get("reasoning_content")
        or meta.get("reasoning_content")
        or getattr(result, "reasoning_content", None)
        or ""
    )


def invoke_llm(
    prompt: str,
    *,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    attempts: int = 3,
    base_delay: float = 2.0,
) -> str:
    """调用模型并返回文本；对超时/连接类瞬时错误做有限重试，耗尽后抛出。

    model 可指定节点专用模型（如审查/测试用非推理模型），为空则用主力模型。
    provider 可指定节点专用供应商（如 planner 走 deepseek），为空则用全局 provider。
    max_tokens 可逐节点覆盖生成预算（如 tester 输出长脚本）；None 回退全局 settings.max_tokens。
    timeout 可逐节点覆盖墙钟超时（如 planner/coder 用推理模型放宽到 600s）；None 回退全局 settings.llm_timeout。
    单个请求用墙钟超时兜底：底层 SDK 的 timeout 有时拦不住「连接挂起」，
    这里把 llm.invoke 放进守护线程，超过 timeout 秒即放弃，把「挂起」转成可捕获的 TimeoutError。
    """
    if timeout is None:
        timeout = settings.llm_timeout
    llm = get_model(model, provider=provider, max_tokens=max_tokens, timeout=timeout)
    for i in range(attempts):
        t0 = time.time()
        try:
            result = _call_with_timeout(lambda: llm.invoke(prompt), timeout)
            if result is _TIMEOUT:
                raise TimeoutError(f"LLM 调用超过墙钟上限 {timeout:.0f}s")
            content = _extract_content(result)
            print(f"[llm] 模型返回，耗时 {time.time() - t0:.1f}s", flush=True)
            return content
        except Exception as exc:
            if not _is_transient(exc) or i == attempts - 1:
                raise
            print(
                f"[llm] 瞬时错误（{type(exc).__name__}），{base_delay * (i + 1):.0f}s 后重试…",
                flush=True,
            )
            time.sleep(base_delay * (i + 1))


def invoke_json(
    prompt: str,
    *,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    attempts: int = 2,
):
    """调用模型并解析其 JSON 输出；解析失败时带纠错提示重试，耗尽后抛出。"""
    correction = (
        "（注意：上一条输出不是合法 JSON，已丢弃。请重新只输出一个合法 JSON 对象，"
        "确保字符串内的换行、双引号与反斜杠都正确转义（代码里的正则如 \\d 必须写成 \\\\d），"
        "不要截断、不要输出解释文字。）"
    )
    for i in range(attempts):
        try:
            return extract_json(
                invoke_llm(prompt, model=model, provider=provider, max_tokens=max_tokens, timeout=timeout)
            )
        except ValueError:
            if i == attempts - 1:
                raise
            print("[llm] JSON 解析失败，带纠错提示重试…", flush=True)
            prompt = prompt + correction
