"""可观测指标上报：run 结束时把终态计数推送到 Prometheus Pushgateway。

采用「push 模型」——CLI 进程跑完即退，Prometheus 拉取式采集抓不到短命进程，
所以 run/resume 结束把指标一次性推给 Pushgateway，再由 Prometheus 从 Pushgateway 抓取。

开关：`CODING_AGENT_METRICS_PUSHGATEWAY`（对应配置 `settings.metrics_pushgateway`）
设为 Pushgateway 地址（如 http://localhost:9092）即启用；留空则完全跳过。

降级：未安装 prometheus-client、或 Pushgateway 不可达时静默跳过，绝不影响主流程——
与项目「韧性降级」的一贯哲学一致（可观测性不应拖垮业务）。
"""

from __future__ import annotations

import socket

from coding_agent.config import settings


def emit_run_metrics(result: dict, thread_id: str, duration_s: float) -> None:
    """在 run/resume 结束后上报本次运行的终态指标。

    指标为一组 gauge（每次运行是独立时间序列，用 run=thread_id 分组），
    记录本次运行的：总耗时、终态（done/failed）、各状态任务数。
    """
    gateway = (settings.metrics_pushgateway or "").strip()
    if not gateway:
        return

    try:
        from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
    except ImportError:
        return  # 未安装 prometheus-client（未装 monitoring extra），静默跳过

    tasks = result.get("tasks") or []
    by_status: dict[str, int] = {}
    for t in tasks:
        s = t.get("status") or "unknown"
        by_status[s] = by_status.get(s, 0) + 1
    overall = result.get("status") or "unknown"

    labels = {
        "language": settings.language,
        "run_mode": "mock" if settings.mock else "real",
        "instance": socket.gethostname(),
    }

    registry = CollectorRegistry()

    def _gauge(name: str, doc: str) -> Gauge:
        return Gauge(name, doc, list(labels), registry=registry)

    _gauge("coding_agent_run_status", "本次运行终态（1=done，0=其他）").labels(**labels).set(
        1 if overall == "done" else 0
    )
    _gauge("coding_agent_run_duration_seconds", "本次运行耗时（秒）").labels(**labels).set(duration_s)
    _gauge("coding_agent_tasks_total", "本次运行任务总数").labels(**labels).set(len(tasks))
    for status in ("done", "failed", "blocked", "pending"):
        _gauge(f"coding_agent_tasks_{status}", f"本次运行 {status} 状态任务数").labels(**labels).set(
            by_status.get(status, 0)
        )

    try:
        # run=thread_id 分组：每次运行一个独立时间序列，便于按单次运行复盘；
        # 代价是 Pushgateway 里会累积历史分组，见 monitoring/README.md 的清理说明。
        push_to_gateway(gateway, job="coding_agent", grouping_key={"run": thread_id}, registry=registry)
    except Exception:
        return  # Pushgateway 不可达时静默降级
