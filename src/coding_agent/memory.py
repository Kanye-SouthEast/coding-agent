"""Milvus 向量记忆：把已写代码索引进去，供 coder/reviewer 按需检索相关上下文。

- 分块：Python 代码按 AST 顶层定义（函数/类）切块，非 Python 回退到按行切块。
- 检索：先向量召回候选，再用重排序模型（rerank）精排，取 top-k。
- 嵌入/重排模型走硅基流动中转，与 Milvus_basic.py 一致。
- Milvus 不可用时降级（索引跳过 / 检索返回空），不阻断主流程。
"""

from __future__ import annotations

import ast
import json
import os
import sys
import threading

from coding_agent.config import settings

_client = None
_embedder = None

_TIMEOUT = object()  # 超时哨兵：与正常返回值区分


def _call_with_timeout(fn, seconds, *args, **kwargs):
    """在守护线程里跑 fn，超过 seconds 秒即放弃并返回 _TIMEOUT。

    Milvus 在 collection 损坏（如 segcore 2011 缺 meta.json）时，search/insert 往往不是
    抛异常，而是无限期挂起（等 collection 加载完成）。单靠 RPC 的 timeout 拦不住这种
    「等加载」，所以这里用线程 + 墙钟时间兜底：超时就丢弃该线程，主流程继续降级。
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


def _get_embedder():
    global _embedder
    if _embedder is None:
        from langchain_openai import OpenAIEmbeddings

        _embedder = OpenAIEmbeddings(
            model=settings.embedding_model,
            base_url=os.getenv("SILICONFLOW_BASE_URL") or settings.base_url,
            api_key=os.getenv("SILICONFLOW_API_KEY"),
        )
    return _embedder


def _get_client():
    global _client
    if _client is None:
        from pymilvus import MilvusClient

        # 显式设 timeout：作为每个 RPC 的 deadline。但注意，collection 损坏时 search
        # 会在「等加载」处挂起、不抛异常，这个 timeout 拦不住——真正的兜底是
        # search/index_files 外层的 _call_with_timeout 墙钟超时。
        _client = MilvusClient(uri=settings.milvus_url, timeout=settings.milvus_timeout)
    return _client


def _ensure_collection(client, name: str | None = None, dim: int | None = None) -> None:
    name = name or settings.milvus_collection
    dim = dim or settings.embedding_dim
    if not client.has_collection(name):
        client.create_collection(
            collection_name=name,
            dimension=dim,
            enable_dynamic_field=True,
            auto_id=True,
        )


# ---- 分块 ----


def _node_source(node, lines: list[str]) -> str:
    """取 AST 节点对应的源码（含装饰器）。"""
    start = node.lineno
    for dec in getattr(node, "decorator_list", []):
        start = min(start, dec.lineno)
    end = getattr(node, "end_lineno", node.lineno)
    return "\n".join(lines[start - 1 : end])


def _chunk_python(text: str) -> list[str]:
    """按 AST 顶层定义切块：每个函数/类一个块，其余语句归入模块头块。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    chunks: list[str] = []
    header: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if header:
                chunks.append("\n".join(header))
                header = []
            seg = _node_source(node, lines)
            if seg.strip():
                chunks.append(seg)
        else:
            header.extend(_node_source(node, lines).splitlines())
    if header:
        chunks.append("\n".join(header))
    return [c for c in chunks if c.strip()]


def _chunk(text: str, size: int = 800) -> list[str]:
    """优先 AST 分块，失败则回退到按行切块。"""
    chunks = _chunk_python(text)
    if chunks:
        return chunks
    lines = text.splitlines()
    out, buf, n = [], [], 0
    for line in lines:
        buf.append(line)
        n += len(line) + 1
        if n >= size:
            out.append("\n".join(buf))
            buf, n = [], 0
    if buf:
        out.append("\n".join(buf))
    return out or [""]


# ---- 索引 / 检索 ----


def _index_impl(files: dict[str, str]) -> int:
    """实际写库逻辑，跑在带墙钟超时的线程里。"""
    client = _get_client()
    _ensure_collection(client)
    embedder = _get_embedder()
    rows = []
    for path, content in files.items():
        for chunk in _chunk(content):
            rows.append(
                {
                    "path": path,
                    "text": chunk,
                    "vector": embedder.embed_query(chunk),
                }
            )
    if rows:
        client.insert(settings.milvus_collection, rows)
    return len(rows)


def index_files(files: dict[str, str]) -> int:
    """把文件内容分块写入 Milvus，返回写入条数。失败/超时时降级返回 0。"""
    if not files:
        return 0
    try:
        result = _call_with_timeout(_index_impl, settings.milvus_timeout, files)
        if result is _TIMEOUT:
            print("[memory] 索引超时（已降级跳过）", file=sys.stderr)
            return 0
        return result
    except Exception as e:  # Milvus 不可用不阻断主流程
        print(f"[memory] 索引失败（已降级跳过）: {e}", file=sys.stderr)
        return 0


def _rerank(query: str, docs: list[tuple[str, str]], top_n: int) -> list[tuple[str, str]]:
    """用重排序模型对候选精排，返回 top_n（失败时回退向量序）。"""
    try:
        import requests

        base = (os.getenv("SILICONFLOW_BASE_URL") or settings.base_url).rstrip("/")
        key = os.getenv("SILICONFLOW_API_KEY")
        r = requests.post(
            base + "/rerank",
            json={
                "model": settings.rerank_model,
                "query": query,
                "documents": [t for _, t in docs],
                "top_n": top_n,
            },
            headers={"Authorization": "Bearer " + key},
            timeout=30,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        ordered = [
            docs[item["index"]]
            for item in results
            if 0 <= item.get("index", -1) < len(docs)
        ]
        return (ordered or docs[:top_n])[:top_n]
    except Exception as e:
        print(f"[memory] rerank 失败（回退向量序）: {e}", file=sys.stderr)
        return docs[:top_n]


def _search_impl(query: str, k: int) -> list[str]:
    """实际检索逻辑，跑在带墙钟超时的线程里。"""
    client = _get_client()
    if not client.has_collection(settings.milvus_collection):
        return []
    vec = _get_embedder().embed_query(query)
    limit = settings.rerank_candidates if settings.rerank_model else k
    res = client.search(
        collection_name=settings.milvus_collection,
        data=[vec],
        limit=max(k, limit),
        output_fields=["path", "text"],
    )
    hits = res[0] if res else []
    docs = [
        (h.get("entity", {}).get("path", "?"), h.get("entity", {}).get("text", ""))
        for h in hits
    ]
    if settings.rerank_model and len(docs) > k:
        docs = _rerank(query, docs, k)
    else:
        docs = docs[:k]
    return [f"# {p}\n{t}" for p, t in docs]


def search(query: str, k: int = 4) -> list[str]:
    """检索与 query 最相关的代码片段：向量召回 → 重排序 → top-k。失败/超时返回 []。"""
    try:
        result = _call_with_timeout(_search_impl, settings.milvus_timeout, query, k)
        if result is _TIMEOUT:
            print("[memory] 检索超时（已降级跳过）", file=sys.stderr)
            return []
        return result
    except Exception as e:
        print(f"[memory] 检索失败（已降级跳过）: {e}", file=sys.stderr)
        return []


def retrieve_context(query: str, k: int = 4) -> str:
    """检索相关代码并格式化为可直接拼进 prompt 的文本（空串表示无结果）。"""
    return "\n\n".join(search(query, k=k))


# ---- 计划记忆：把 (需求 → 计划) 存进 Milvus，供 planner 做语义 few-shot ----
# 与上面代码记忆共用 client/embedder/rerank/超时兜底，只是集合不同（需求文本 vs 代码块
# 的语义空间不同）。用途是「相近需求 → 相近拆解」：新需求与历史需求语义相近时，检索出历史
# 计划注入 planner prompt 作参考，让拆解风格/粒度一致。注意这是「引导」而非「缓存复用」——
# 历史计划只在 prompt 里当例子，最终计划仍由 planner 针对当前需求重新生成，避免拿相近但
# 不相同的契约去复用导致下游 coder/tester 越界。


def _index_plan_impl(requirement: str, plan: list) -> int:
    client = _get_client()
    _ensure_collection(client, settings.planner_plan_collection, settings.embedding_dim)
    embedder = _get_embedder()
    rows = [
        {
            "requirement": requirement,
            "plan": json.dumps(plan, ensure_ascii=False),
            "vector": embedder.embed_query(requirement),
        }
    ]
    client.insert(settings.planner_plan_collection, rows)
    return 1


def index_plan(requirement: str, plan: list) -> int:
    """把一次成功的 (需求 → 计划) 写入 Milvus。失败/超时降级返回 0，不阻断主流程。"""
    if not requirement or not plan:
        return 0
    try:
        result = _call_with_timeout(
            _index_plan_impl, settings.milvus_timeout, requirement, plan
        )
        if result is _TIMEOUT:
            print("[memory] 计划索引超时（已降级跳过）", file=sys.stderr)
            return 0
        return result
    except Exception as e:
        print(f"[memory] 计划索引失败（已降级跳过）: {e}", file=sys.stderr)
        return 0


def _search_plans_impl(query: str, k: int) -> list[dict]:
    client = _get_client()
    if not client.has_collection(settings.planner_plan_collection):
        return []
    vec = _get_embedder().embed_query(query)
    limit = settings.rerank_candidates if settings.rerank_model else k
    res = client.search(
        collection_name=settings.planner_plan_collection,
        data=[vec],
        limit=max(k, limit),
        output_fields=["requirement", "plan"],
    )
    hits = res[0] if res else []
    docs = [
        (h.get("entity", {}).get("requirement", ""), h.get("entity", {}).get("plan", ""))
        for h in hits
    ]
    if settings.rerank_model and len(docs) > k:
        # 只在「需求文本」上重排（plan JSON 只是载荷，不该参与重排）
        ordered = _rerank(query, [(req, req) for req, _ in docs], k)
        ordered_reqs = [req for req, _ in ordered]
        plan_by_req: dict[str, str] = {}
        for req, plan_json in docs:
            plan_by_req.setdefault(req, plan_json)
        docs = [(req, plan_by_req.get(req, "")) for req in ordered_reqs]
    else:
        docs = docs[:k]
    out = []
    for req, plan_json in docs:
        try:
            plan = json.loads(plan_json)
        except Exception:
            continue
        out.append({"requirement": req, "plan": plan})
    return out


def search_plans(query: str, k: int = 3) -> list[dict]:
    """语义检索与 query 相近的历史 (需求→计划) 对。失败/超时返回 []。"""
    try:
        result = _call_with_timeout(_search_plans_impl, settings.milvus_timeout, query, k)
        if result is _TIMEOUT:
            print("[memory] 计划检索超时（已降级跳过）", file=sys.stderr)
            return []
        return result
    except Exception as e:
        print(f"[memory] 计划检索失败（已降级跳过）: {e}", file=sys.stderr)
        return []
