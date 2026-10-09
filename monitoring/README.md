# 自建监控（Prometheus + Pushgateway + Grafana + node-exporter）

配套 `src/coding_agent/metrics.py`，负责把每次 agent 运行的终态指标推给 Pushgateway，
Prometheus 再从 Pushgateway 抓取、Grafana 展示。LLM 层的 trace/成本/延迟走 LangSmith（见根目录 `.env`），
两者互补、不重叠。

## 快速开始

1. 拉全家桶：

   ```bash
   docker compose -f monitoring/docker-compose.yml up -d
   ```

2. 让应用上报指标（`.env` 或环境变量）：

   ```dotenv
   CODING_AGENT_METRICS_PUSHGATEWAY=http://localhost:9092
   ```

   装上报依赖：

   ```bash
   pip install -e ".[monitoring]"
   ```

3. 跑一次 agent，结束后指标即推入 Pushgateway：

   ```bash
   python -m coding_agent.main "fibonacci demo" -y
   ```

4. 打开 Grafana `http://localhost:3000`（admin/admin），添加数据源 Prometheus（`http://prometheus:9090`），
   即可查询 `coding_agent_run_status`、`coding_agent_run_duration_seconds`、`coding_agent_tasks_*` 等指标。

## 上报的指标

| 指标 | 类型 | 含义 |
|---|---|---|
| `coding_agent_run_status` | gauge | 本次运行终态（1=done，0=其他） |
| `coding_agent_run_duration_seconds` | gauge | 本次运行耗时（秒） |
| `coding_agent_tasks_total` | gauge | 本次运行任务总数 |
| `coding_agent_tasks_{done,failed,blocked,pending}` | gauge | 各状态任务数 |

标签：`language` / `run_mode`（mock|real）/ `instance`（主机名）/ `run`（thread_id，Pushgateway 分组键）。

## 端口约定

| 服务 | 宿主端口 | 说明 |
|---|---|---|
| Prometheus | 9090 | |
| Pushgateway | **9092** | 容器内 9091；宿主避开 Milvus 的 9091 指标口 |
| Grafana | 3000 | |
| node-exporter | 9100 | |

## 注意事项

- **指标是「本次运行快照」**：每次 run 以 `run=thread_id` 分组推入，形成独立时间序列，便于按单次运行复盘。
  代价是 Pushgateway 会累积历史分组。清理方式（可选）：`curl -X DELETE http://localhost:9092/metrics/job/coding_agent`
  或定期重启 Pushgateway（数据只存内存，默认不落盘）。
- **Milvus 抓取**：`prometheus.yml` 里用 `host.docker.internal:9091` 抓 Milvus 指标。仅 Docker Desktop（Win/Mac）开箱即用；
  Linux 需给 prometheus 加 `extra_hosts: ["host.docker.internal:host-gateway"]`。
- **降级**：未装 `prometheus-client` 或 Pushgateway 不可达时，`emit_run_metrics` 静默跳过，绝不影响主流程。
