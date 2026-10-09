"""项目配置：通过环境变量 / .env 覆盖（前缀 CODING_AGENT_）。"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：config.py 位于 src/coding_agent/，向上三级即项目根。
# 把 workspace / checkpoint 锚定到固定位置，避免相对路径随启动目录漂移。
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODING_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    provider: str = "siliconflow"  # siliconflow | moonshot | anthropic | openai | deepseek
    model: str = "Qwen/Qwen3-Coder-30B-A3B-Instruct"
    # 主力模型（供 reviewer/tester 用；留空则回退到 model）。
    # 各节点可单独指定模型；留空则回退到 model（主力模型）。
    # 分工原则：planner 和 coder 用推理模型（拆解/编码质量更高，但思考久，故单独放宽到
    # planner_timeout/coder_timeout=600s，答案在 reasoning_content 已兜底提取）；
    # reviewer/tester 仍用非推理模型（要快）。coder 的 bug 由 reviewer+tester 对拍兜底。
    planner_model: str = "deepseek-v4-flash"  # planner 用 DeepSeek 推理模型（答案在 reasoning_content，已兜底提取；超时 planner_timeout）
    planner_provider: str = "deepseek"  # planner 专用供应商（如 deepseek）；留空则用全局 provider
    coder_model: str = "moonshotai/Kimi-K2.7-Code"  # coder 用 SiliconFlow 推理模型（答案可能在 reasoning_content，已兜底提取；超时 coder_timeout）
    coder_provider: str = ""  # coder 专用供应商（留空用全局 provider=siliconflow；切 deepseek 推理则填 "deepseek"）
    reviewer_model: str = ""
    tester_model: str = ""
    max_tokens: int = 0  # 生成 token 上限；0 = 不限制（交给模型默认）
    tester_max_tokens: int = (
        8192  # tester 生成完整测试脚本（含暴力参考实现+多组用例），输出较长，单独放宽 token 预算；0 = 用全局 max_tokens
    )
    base_url: str = "https://api.siliconflow.cn/v1"  # siliconflow 端点（OpenAI 兼容）
    mock: bool = True  # 无需 API key 跑通流程
    workspace: Path = PROJECT_ROOT / "workspace"  # 代码落盘目录（锚定到项目根）
    max_iterations: int = 8  # 调试闭环上限（边界类 bug 往往需多轮对拍才能收敛，略放宽）
    max_review_vetoes: int = 2  # reviewer 连续否决 N 次后强制进测试，避免静态审查饿死 tester（对拍）
    max_same_failures: int = 3  # 连续 N 次完全相同的测试失败（无进展）→ 提前标 failed 推进，避免空转到迭代耗尽
    sandbox: str = "docker"  # 沙箱后端：docker | subprocess
    language: str = "python"  # 实现语言：python | javascript | cpp | java | go | rust（决定镜像与测试器）
    llm_timeout: float = 180.0  # LLM 单次请求超时（秒）
    planner_timeout: float = 600.0  # planner 用推理模型思考久，单独放宽墙钟超时（秒）
    coder_timeout: float = 600.0  # coder 用推理模型，同样放宽
    llm_max_retries: int = 1  # LLM 请求失败重试次数
    coder_syntax_retries: int = 3  # coder 生成代码后的 Python 语法自修复次数（compile() 检查）
    checkpoint_uri: str = str(
        PROJECT_ROOT / "checkpoints.sqlite"
    )  # 持久化 checkpoint 的 SQLite 文件路径（锚定到项目根）
    interrupt_before_plan: bool = True  # 规划后暂停，人工批准计划再执行
    planner_cache_file: str = str(
        PROJECT_ROOT / ".planner_cache.json"
    )  # 计划缓存：同一需求复用上次拆解，保证 planner 稳定
    planner_plan_collection: str = (
        "coding_agent_plans"  # Milvus 集合：存 (需求→计划) 对，供 planner 语义检索相近历史计划做 few-shot
    )
    planner_fewshot_k: int = (
        3  # 计划缓存未命中时，从 Milvus 检索相近历史计划注入为 few-shot 的条数（0=关闭语义 few-shot）
    )
    milvus_url: str = "http://localhost:19530"  # Milvus 服务器地址
    milvus_timeout: float = 10.0  # Milvus 单次操作墙钟超时（秒）：embed+检索+重排整体预算，卡死时快速降级
    milvus_collection: str = "coding_agent_code"  # 向量集合名
    embedding_model: str = "BAAI/bge-m3"  # 嵌入模型（硅基流动中转）
    embedding_dim: int = 1024  # 嵌入向量维度
    rerank_model: str = "BAAI/bge-reranker-v2-m3"  # 重排序模型（空串则关闭 rerank）
    rerank_candidates: int = 12  # 向量召回候选数（rerank 前）
    metrics_pushgateway: str = ""  # Prometheus Pushgateway 地址（如 http://localhost:9092）；留空则关闭指标上报


settings = Settings()
