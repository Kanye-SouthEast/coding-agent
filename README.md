# coding-agent

基于 **LangGraph** 的自主编程多 Agent 框架：给定一句需求，自动完成「规划 → 编码 → 审查 → 测试」的闭环，测试失败时自动回退重写，直到通过或达到迭代上限。

## 特性

- **多 Agent 流水线**：planner / scheduler / coder / reviewer / tester 五个节点，各司其职
- **精确接口契约**：planner 为每个任务生成 `signature`（精确签名）+ `contract`（合法/非法输入、异常行为、职责边界）+ `invalid_inputs`（非法输入清单），coder 照签名实现、tester 照清单对拍，杜绝接口猜测与越界断言
- **调试闭环**：审查意见或测试失败自动回退 coder，每个任务有独立的迭代预算；另有「无进展熔断」（连续相同失败提前标 failed）与「审查否决上限」（否决 N 次强制进测试）防止空转
- **带依赖的任务调度**：需求拆成任务 DAG，按依赖顺序推进，测试通过才标记完成；失败会正确终结（不再无限重试），并标记被依赖卡住的任务
- **沙箱隔离**：不可信代码默认在 Docker 容器里执行（断网、限内存、限 CPU）
- **多语言**：Python / Node.js / C++ / Java / Go / Rust，沙箱镜像与测试器随语言自动切换
- **人工介入（interrupt）**：规划后暂停人工批准计划，可断点续跑
- **持久化 checkpoint**：状态落 SQLite，支持 `--resume` / `--inspect` / `--history`
- **向量检索增强（Milvus）**：AST 分块 + 重排序，给 coder/reviewer 注入相关代码；另存「需求→计划」集合，供 planner 做语义 few-shot
- **依赖感知的上下文注入**：coder / tester 只注入「当前任务 + 其传递依赖」已产出的文件（而非全量 `files`），文件随任务变多时控制 prompt 长度、剔除无关噪声；缺 `task_files` 等旧数据时宁多勿少回退全量
- **计划稳定性**：同一需求复用上次拆解（本地 JSON 缓存）+ 相近需求语义检索注入 few-shot，保证拆解一致；按需求关键词注入解析器/算法等专项契约
- **可观测**：审查意见与测试失败详情实时打印，结束后与事后都能查看
- **外部监控**：LLM 层走 LangSmith（trace/成本/延迟，带语言/供应商元数据），基础设施与应用终态指标走自建 Prometheus + Pushgateway + Grafana；配套 GitHub Actions CI（lint + test + build）
- **韧性降级**：LLM 单次请求有墙钟超时兜底（挂起自动转失败）；规划失败降级为单任务、审查失败放行交测试兜底、测试器自身故障回 tester 重试，不因单点故障崩溃
- **按节点选模型**：规划/编码用推理模型（深度推理），审查/测试用非推理模型（快、稳、省）；每个节点可独立指定模型与供应商、留空回退主力模型

## 核心流程

```
planner → (approve 人工批准) → scheduler → coder → reviewer → tester
              ↑  ┌───────────────────────────────────────────────┘
              └──┘ 审查意见/测试失败回退 coder；通过回 scheduler 推进下一任务（按依赖）
```

1. **planner** 把需求拆成带依赖的任务列表
2. **approve**（可选）暂停，人工批准任务计划
3. **scheduler** 按依赖挑选下一个就绪任务，标记上一个任务的完成/失败
4. **coder** 生成代码写入工作区（Python 生成后先做语法自检，有语法错误自动重写）
5. **reviewer** 静态审查，有意见则回退 coder 修改；连续否决达到 `max_review_vetoes` 次时强制进测试
6. **tester** 生成测试脚本并在沙箱里运行，失败则回退 coder 修复；测试器自身故障（生成/解析/沙箱异常）则回 tester 自纠错重试

### 任务状态机

每个任务在运行中会经历：`pending → done`（测试通过）或 `pending → failed`（迭代上限耗尽仍失败）。依赖已失败任务的任务会被标记为 `blocked`。最终整体状态为 `done`（全部完成）或 `failed`（存在失败/受阻任务）。

调试闭环里的三个计数器——迭代次数 `iteration`、审查否决 `review_vetoes`、无进展 `same_fail_streak`——都按任务独立计数，调度器切换任务时统一复位。

## 目录结构

```
.
├── pyproject.toml          # 依赖 + 入口脚本 + pytest 配置
├── .env                    # 环境变量（已 gitignore，含 API key）
├── .planner_cache.json     # 计划缓存（同一需求复用上次拆解）
├── .github/workflows/ci.yml # CI 流水线（GitHub Actions：lint + test + build + docker 冒烟）
├── monitoring/             # 自建监控（Prometheus + Pushgateway + Grafana，详见 monitoring/README.md）
├── tests/                  # pytest 单元测试（图路由/调度/检索/语言分发/文件防护等）
├── checkpoints.sqlite      # 运行产生的 checkpoint（默认锚定到项目根）
├── workspace/              # 运行产物：生成的代码与测试（默认锚定到项目根）
└── src/coding_agent/
    ├── config.py           # 配置（pydantic-settings，前缀 CODING_AGENT_）
    ├── state.py            # 全局共享状态 AgentState + 依赖感知文件剪枝（files_for_task）
    ├── jsonx.py            # 稳健的 JSON 提取工具
    ├── llm.py              # 模型工厂（SiliconFlow / Moonshot / OpenAI / Anthropic / DeepSeek）+ 墙钟超时与重试 + 推理模型 reasoning_content 兜底
    ├── memory.py           # Milvus 向量检索（代码记忆：嵌入 + 分块 + 索引 + 检索 + 重排序；计划记忆：需求→计划 few-shot）
    ├── runtimes.py         # 语言运行时注册表（镜像 + 测试器按语言分发）
    ├── graph.py            # LangGraph 图 + 条件路由（调试闭环 + 否决上限 + 无进展熔断）+ 人工审批中断
    ├── main.py             # CLI 入口
    ├── metrics.py          # 指标上报（run 终态 → Prometheus Pushgateway，全程降级）
    ├── agents/             # 5 个 Agent 节点
    │   ├── planner.py      #   规划：需求 → 带精确契约（signature/contract/invalid_inputs）的任务 DAG + 场景识别 + 计划缓存/few-shot
    │   ├── scheduler.py    #   调度：按依赖推进任务、标记完成/失败/受阻、复位各计数器
    │   ├── coder.py        #   编码：生成代码写入工作区 + Python 语法自修复 + 孤儿文件清理
    │   ├── reviewer.py     #   审查：静态审查（具体反例门槛），输出修改意见 + 否决计数
    │   └── tester.py       #   测试：生成对拍测试脚本并运行 + 非法输入清单对拍 + 自纠错重试 + 入口任务识别
    └── tools/              # 工具层
        ├── files.py        #   文件读写（限制路径越界）
        ├── sandbox.py      #   沙箱后端（docker 隔离 / subprocess 本地）
        └── shell.py        #   Shell 执行（委托给沙箱后端）
```

## 快速开始

### 1. 安装

```bash
# 建议在独立的 conda 环境里安装；依赖 langgraph / langchain-openai / pymilvus 等
pip install -e ".[dev]"
```

要求 Python >= 3.11。

> 若未安装、直接从源码跑，需把 `src/` 加入 `PYTHONPATH`：
> `PYTHONPATH=src python -m coding_agent.main ...`（Windows PowerShell：`$env:PYTHONPATH="src"`）。

### 2. 离线试跑（mock 模式，无需 API key）

mock 模式用内置的 Python 演示任务跑通完整闭环，并故意在第一个任务写出有 bug 的 `fib` 以演示「测试失败 → 回退重写 → 通过」：

```bash
python -m coding_agent.main "fibonacci demo" -y
# 或安装后直接: coding-agent "fibonacci demo" -y
```

> mock 模式默认用 Docker 沙箱执行测试，需要本机有 Docker 且已 `docker pull python:3.12-slim`；
> 没有 Docker 时设 `CODING_AGENT_SANDBOX=subprocess`（在本机直接执行，仅适合演示）。

### 3. 真实 LLM 模式

在项目根目录的 `.env` 里填好 API key（文件已 gitignore）：

```dotenv
CODING_AGENT_PROVIDER=siliconflow
SILICONFLOW_API_KEY=sk-xxxx
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
DEEPSEEK_API_KEY=sk-xxxx            # planner 默认走 DeepSeek，需同时配
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

然后：

```bash
python -m coding_agent.main "实现一个数学工具库" --no-mock -y
```

默认按节点分工选模型：planner 走 **DeepSeek** 推理模型 `deepseek-v4-flash`，coder 走 **硅基流动 SiliconFlow 中转**的推理模型 `moonshotai/Kimi-K2.7-Code`，reviewer / tester 走主力模型 `Qwen/Qwen3-Coder-30B-A3B-Instruct`（非推理、快而省）。切换供应商只需改 `.env`：

| 供应商 | `CODING_AGENT_PROVIDER` | 需要的 API key |
| ------ | ----------------------- | -------------- |
| 硅基流动 | `siliconflow` | `SILICONFLOW_API_KEY` |
| DeepSeek 官方 | `deepseek` | `DEEPSEEK_API_KEY`（+ `DEEPSEEK_BASE_URL`） |
| Moonshot 官方站 | `moonshot` | `MOONSHOT_API_KEY`（模型改 `kimi-k2.7-code`） |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` |

> 各节点可单独指定供应商（`CODING_AGENT_PLANNER_PROVIDER` / `CODING_AGENT_CODER_PROVIDER`），留空回退全局 `CODING_AGENT_PROVIDER`。

## 命令行参数

```bash
python -m coding_agent.main [需求] [选项]
```

| 参数 | 说明 |
| ---- | ---- |
| `requirement`（位置参数） | 要完成的需求描述，默认演示斐波那契 |
| `--requirement-file` / `-f` | 从 UTF-8 文件读取需求（避开中文命令行乱码），优先级高于位置参数 |
| `--mock` / `--no-mock` | 是否用 mock 演示（默认 `--mock`，真实 LLM 用 `--no-mock`） |
| `--language` | 实现语言：`python` / `javascript` / `cpp` / `java` / `go` / `rust`（默认 `python`） |
| `--thread-id` | 会话 ID，默认由「需求 + 语言」哈希自动生成 |
| `--inspect` | 不运行，只查看该会话已持久化的最新状态 |
| `--history` | 不运行，只列出该会话每一轮的审查意见与测试结果时间线 |
| `--resume` | 从中断点续跑（配合计划审批） |
| `--auto-approve` / `-y` | 自动批准计划，规划后不暂停 |

## 配置项（.env）

所有配置都可用环境变量覆盖，统一前缀 `CODING_AGENT_`。常用项：

| 环境变量 | 默认值 | 说明 |
| -------- | ------ | ---- |
| `CODING_AGENT_PROVIDER` | `siliconflow` | 全局模型供应商：`siliconflow` / `moonshot` / `openai` / `anthropic` / `deepseek` |
| `CODING_AGENT_MODEL` | `Qwen/Qwen3-Coder-30B-A3B-Instruct` | 主力模型名（非推理，reviewer / tester 默认用） |
| `CODING_AGENT_PLANNER_MODEL` | `deepseek-v4-flash` | 规划节点专用模型（DeepSeek 推理模型，答案在 `reasoning_content` 已兜底提取） |
| `CODING_AGENT_PLANNER_PROVIDER` | `deepseek` | 规划节点专用供应商，留空回退全局 `PROVIDER` |
| `CODING_AGENT_CODER_MODEL` | `moonshotai/Kimi-K2.7-Code` | 编码节点专用模型（SiliconFlow 推理模型） |
| `CODING_AGENT_CODER_PROVIDER` | `""` | 编码节点专用供应商，留空回退全局 `PROVIDER` |
| `CODING_AGENT_REVIEWER_MODEL` | `""` | 审查节点专用模型，留空回退主力模型（建议用非推理模型提速） |
| `CODING_AGENT_TESTER_MODEL` | `""` | 测试节点专用模型，留空回退主力模型（建议用非推理模型提速） |
| `CODING_AGENT_MAX_TOKENS` | `0` | 生成 token 上限（0 = 不限制，交给模型默认） |
| `CODING_AGENT_TESTER_MAX_TOKENS` | `8192` | tester 生成完整测试脚本（含暴力参考实现+多组用例），单独放宽 token 预算 |
| `CODING_AGENT_BASE_URL` | `https://api.siliconflow.cn/v1` | OpenAI 兼容端点（siliconflow 默认） |
| `CODING_AGENT_MOCK` | `true` | 是否走 mock 演示 |
| `CODING_AGENT_WORKSPACE` | `<项目根>/workspace` | 代码落盘目录（锚定到项目根，不随启动目录漂移） |
| `CODING_AGENT_MAX_ITERATIONS` | `8` | 每个任务的调试迭代上限 |
| `CODING_AGENT_MAX_REVIEW_VETOES` | `2` | reviewer 连续否决 N 次后强制进测试（对拍），避免静态审查饿死 tester |
| `CODING_AGENT_MAX_SAME_FAILURES` | `3` | 连续 N 次完全相同的测试失败（无进展）→ 提前标 failed，避免空转到迭代耗尽 |
| `CODING_AGENT_SANDBOX` | `docker` | 沙箱后端：`docker` / `subprocess` |
| `CODING_AGENT_LANGUAGE` | `python` | 实现语言：`python` / `javascript` / `cpp` / `java` / `go` / `rust` |
| `CODING_AGENT_LLM_TIMEOUT` | `180` | 单次 LLM 请求墙钟超时（秒），挂起即转失败 |
| `CODING_AGENT_PLANNER_TIMEOUT` | `600` | planner 用推理模型思考久，单独放宽墙钟超时（秒） |
| `CODING_AGENT_CODER_TIMEOUT` | `600` | coder 用推理模型，同样放宽 |
| `CODING_AGENT_LLM_MAX_RETRIES` | `1` | LLM 请求失败重试次数 |
| `CODING_AGENT_CODER_SYNTAX_RETRIES` | `3` | coder 生成代码后的 Python 语法自修复次数（`compile()` 检查） |
| `CODING_AGENT_CHECKPOINT_URI` | `<项目根>/checkpoints.sqlite` | 持久化 checkpoint 文件（锚定到项目根） |
| `CODING_AGENT_INTERRUPT_BEFORE_PLAN` | `true` | 规划后是否暂停人工批准 |
| `CODING_AGENT_PLANNER_CACHE_FILE` | `<项目根>/.planner_cache.json` | 计划缓存文件：同一需求复用上次拆解 |
| `CODING_AGENT_PLANNER_PLAN_COLLECTION` | `coding_agent_plans` | Milvus 集合名（存「需求→计划」对，供 planner 语义 few-shot） |
| `CODING_AGENT_PLANNER_FEWSHOT_K` | `3` | 计划缓存未命中时语义检索相近历史计划注入为 few-shot 的条数（0 = 关闭） |
| `CODING_AGENT_MILVUS_URL` | `http://localhost:19530` | Milvus 服务器地址（也可设成本地文件路径用 Milvus Lite） |
| `CODING_AGENT_MILVUS_TIMEOUT` | `10` | Milvus 单次操作墙钟超时（秒）：embed+检索+重排整体预算，卡死时快速降级 |
| `CODING_AGENT_MILVUS_COLLECTION` | `coding_agent_code` | 代码向量集合名 |
| `CODING_AGENT_EMBEDDING_MODEL` | `BAAI/bge-m3` | 嵌入模型（走硅基流动中转） |
| `CODING_AGENT_EMBEDDING_DIM` | `1024` | 嵌入向量维度 |
| `CODING_AGENT_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | 重排序模型（空串关闭） |
| `CODING_AGENT_RERANK_CANDIDATES` | `12` | 向量召回候选数（重排前） |
| `CODING_AGENT_METRICS_PUSHGATEWAY` | `""` | Prometheus Pushgateway 地址（如 `http://localhost:9092`）；留空则关闭指标上报 |

> 自 0.1.0 起，`workspace` 与 `checkpoint_uri` 的默认值锚定到**项目根目录**（`config.py` 里的 `PROJECT_ROOT`），
> 不再随启动目录变化——旧版本从不同目录启动会产生 `workspace/workspace/` 嵌套和多个 checkpoint 文件的问题已修复。

## 多语言

`CODING_AGENT_LANGUAGE`（或 `--language`）选择实现语言，自动切换沙箱镜像、测试器运行命令与各 prompt：

| 语言 | 键 | 沙箱镜像 | 测试运行 | 开发规范 |
| ---- | -- | -------- | -------- | -------- |
| Python | `python` | `python:3.12-slim` | `python -m tests.test_suite` | 模块级函数，import 复用 |
| Node.js | `javascript` | `node:24-slim` | `node tests/test_suite.js` | `module.exports` 导出 |
| C++ | `cpp` | `gcc:14` | `g++ -std=c++17 -O2 -I. *.cpp tests/main_test.cpp -o /tmp/test_bin && /tmp/test_bin` | `.h` 声明 + `.cpp` 实现（include guard） |
| Java | `java` | `eclipse-temurin:21-jdk` | `javac -d /tmp/classes *.java tests/MainTest.java && java -cp /tmp/classes MainTest` | 一个 public 类一个文件 |
| Go | `go` | `golang:1.23` | `go mod init app; go test . -v` | package + `_test.go` |
| Rust | `rust` | `rust:1.80-slim` | `cargo test` | cargo 库 crate（src/lib.rs）+ 集成测试 |

```bash
python -m coding_agent.main "写一个返回 UTC 时间戳的模块" --no-mock --language go
```

各语言别名（`--language` 不区分大小写）：`py`/`python3` → python，`js`/`node` → javascript，`c++`/`cxx` → cpp，`golang` → go，`rs` → rust。

编译型语言（C++/Java/Go/Rust）约定：编码阶段只生成「可复用函数」、不写 `main()`；测试阶段用固定测试文件名避免跨任务残留冲突。测试文件统一归位到 `tests/` 子目录，与业务代码分开：Python/JS 固定 `tests/test_suite.py` / `tests/test_suite.js`，C++/Java 固定 `tests/main_test.cpp` / `tests/MainTest.java`（编译命令带 `-I.` / `-d`/`-cp` 处理子目录）。例外：Go 的 `_test.go` 需与代码同包，故留在包根目录 `main_test.go`；Rust 本就在 `tests/main_test.rs`。Rust 的 `Cargo.toml` 由框架自动落盘。一个任务可产出多个文件（如 C++ 的 `.h` + `.cpp`）。

当需求明确要求一个可运行入口（命令行工具/CLI）时，planner 允许最后一个任务生成 `main()`（入口任务）。此时 C++ 的测试策略切换为「入口专用」：编译命令排除入口文件 `main.cpp`（否则业务 `main` 与测试脚本 `main` 同是全局唯一符号，链接报 `multiple definition of 'main'`），测试脚本只对拍入口所依赖的复用函数（如 tokenize/evaluate）；入口自身的参数解析/返回码正确性不参与对拍，交人工黑盒验证。入口任务由 tester 按 `signature` 是否含 `main(` 自动识别。仅 C++ 需要此策略：Java 的 `javac` 编译后 `java MainTest` 只跑测试类、Go 的 `go test` / Rust 的 `cargo test` 天然兼容入口、不产生符号冲突。

新增语言：在 `src/coding_agent/runtimes.py` 的 `RUNTIMES` 里加一条（镜像需先 `docker pull`）。mock 模式固定 Python 演示，`--language` 只影响真实 LLM 模式。

### 人工复跑生成的测试

每次运行会把生成的代码与测试写入 `workspace/`（见 `CODING_AGENT_WORKSPACE`），该目录是「最近一次运行」的产物，会被下一次运行覆盖，想保留结果需自行复制。

测试脚本本身就是对拍用例（内含暴力参考实现），在 `workspace/` 目录下直接运行即可人工验证：

| 语言 | 人工运行命令（`cd workspace` 后） | 通过标志 |
| ---- | ------------------------------- | -------- |
| Python | `python -m tests.test_suite` | 打印 `PASS` 且退出码 0 |
| Node.js | `node tests/test_suite.js` | 打印 `PASS` 且退出码 0 |
| C++ | `g++ -std=c++17 -O2 -I. *.cpp tests/main_test.cpp -o test_bin && ./test_bin` | 打印 `PASS` 且退出码 0 |
| Java | `javac -d classes *.java tests/MainTest.java && java -cp classes MainTest` | 打印 `PASS` 且退出码 0 |
| Go | `go mod init app >/dev/null 2>&1; go test . -v` | `go test` 退出码 0 |
| Rust | `cargo test` | 退出码 0 |

> 这些是**独立脚本**（靠 `if __name__ == '__main__'` 直接跑），用 `python` / `node` 执行，**不要用 pytest**——
> pytest 按 `test_*` 命名收集用例，而这些脚本里没有 `test_*` 函数，会收集到 0 条测试。

> 上表命令是 **bash 语法**（用到 `&&`、`>/dev/null 2>&1` 等），请在 Git Bash / WSL / Linux / macOS 下运行。
> Windows PowerShell 5.1 不支持 `&&`：把 `cmd1 && cmd2` 拆成两行（先 `cmd1`，成功后再 `cmd2`），或改用 PowerShell 7（`pwsh`）。

> 测试只在「被测函数 vs 暴力参考实现」之间对拍，能抓住实现错误，但不能证明暴力参考实现本身正确；
> 复核算法正确性请直接读 `workspace/` 下对应语言的源文件。

## 沙箱与安全

执行 LLM 生成的不可信代码时，默认用 Docker 沙箱隔离（`CODING_AGENT_SANDBOX=docker`）：

- **docker**：在按语言选择的镜像容器里执行，限制内存 512m / CPU 1 核、`--network none` 断网、挂载 workspace 到 `/workspace`，超时 30s
- **subprocess**：直接在本机进程执行（无隔离，仅适合可信 / 演示场景）

文件写入统一经过工作区路径校验，`../` 或绝对路径逃逸会被拒绝。

## 向量检索（Milvus）

coder / reviewer 生成前会把任务描述做向量检索，从 Milvus 拉取相关代码片段注入 prompt；coder 写文件后自动索引进库。

- 分块：Python 按 AST 顶层定义（函数/类）切块，非 Python 回退按行切块
- 检索：先向量召回 `CODING_AGENT_RERANK_CANDIDATES` 条，再用重排序模型精排取 top-k
- **计划记忆**：另用一个集合（`CODING_AGENT_PLANNER_PLAN_COLLECTION`）存「需求→计划」对；planner 在本地缓存未命中时语义检索相近历史计划注入为 few-shot，引导拆解风格/粒度一致（引导而非照抄，最终计划仍由 planner 重新生成）
- Milvus 不可用时自动降级（跳过索引 / 检索返回空），不阻断主流程

> 注意：仅在**真实模式**（`--no-mock`）下才会与 Milvus 交互，mock 模式完全不碰。嵌入/重排走硅基流动 API，检索/写入走 Milvus RPC。

需要本地 standalone Milvus（默认 `localhost:19530`），可用官方镜像拉起：

```bash
docker run -d --name milvus-standalone -p 19530:19530 -p 9091:9091 \
  milvusdb/milvus:v3.0.0 milvus run standalone
```

> Milvus v3.0.0 单机默认走嵌入式 etcd，在部分环境会 panic；更稳妥的是用 docker-compose 起「etcd + minio + standalone」三件套，详见 Milvus 官方 standalone 部署文档。

也可把 `CODING_AGENT_MILVUS_URL` 设成本地文件路径（如 `./milvus.db`），让 `MilvusClient` 走 **Milvus Lite** 嵌入式模式，免去独立服务——适合快速本地验证。

## 人工介入与断点续跑

规划完成后默认暂停，人工批准任务 DAG 再继续执行（`interrupt_before` 挂在 `approve` 节点上）：

```bash
python -m coding_agent.main "实现一个数学工具库" --no-mock        # 打印计划并等待批准
python -m coding_agent.main "实现一个数学工具库" --no-mock -y      # --auto-approve 自动批准
python -m coding_agent.main --resume --thread-id <id>             # 从中断点续跑（拒绝后可继续）
```

- 会话 ID（`thread_id`）默认由「需求 + 语言」哈希生成，同一需求跨多次运行复用同一会话，不同语言用不同会话
- 状态持久化到 SQLite（`CODING_AGENT_CHECKPOINT_URI`），可随时 `--inspect` 查看
- 拒绝后计划已持久化，可稍后用 `--resume` 从暂停点继续；`CODING_AGENT_INTERRUPT_BEFORE_PLAN=false` 可默认关闭

## 观察运行结果

审查意见与测试失败详情现在全程可见：

- **实时**：审查不通过、测试失败时会把具体内容打到 stderr——`[reviewer] 审查未通过：<意见>`、`[tester] 测试未通过：\n<完整测试输出>`
- **结束摘要**：打印每个任务的状态（`done` / `failed` / `blocked`），以及审查意见与测试结果（非空时）
- **事后查看最新状态**：

  ```bash
  python -m coding_agent.main --inspect --thread-id <id>
  ```

- **回看每一轮（调试闭环的时间线）**：

  ```bash
  python -m coding_agent.main --history --thread-id <id>
  ```

  `--history` 会按时间正序列出每次「审查未通过 / 测试未通过 / 测试通过」及其所属轮次与任务，方便定位某次失败发生在第几轮、审查意见和测试输出是什么。

> 调试闭环里 `review_feedback` / `test_result` 会被不断覆盖，最终摘要里的测试结果只保留**最后一次**；想看到每一轮的完整轨迹用 `--history`。

## 测试

pytest 覆盖图路由（含否决上限、无进展熔断、`FAIL: [tester]` 自纠错回退）、调度依赖（含失败终态与 blocked）、依赖感知文件剪枝（files_for_task）、检索排序、语言分发、文件路径防护、JSON 稳健解析（尾逗号/非法转义修复）、planner 场景识别与计划缓存、tester 非法输入清单对拍与入口任务识别（C++ 入口编译命令）、coder 语法自修复、多文件产出与 mock 端到端调试闭环；测试强制 mock + subprocess，离线即可跑，无需 Docker / LLM / Milvus：

```bash
python -m pytest
```

## 监控与可观测

分两层，各管一段：**LLM 层**走 LangSmith（trace / 成本 / 延迟 / 质量），**基础设施层**走自建 Prometheus + Pushgateway + Grafana（应用指标 / 告警）。

### LangSmith —— LLM 层（零埋点）

在 `.env` 配好 LangSmith，LangGraph 就会自动把每个节点、每次 LLM 调用串成 trace：

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=coding-agent
LANGSMITH_ENV=dev          # dev | ci | prod，区分本地与 CI 里的 trace
```

`main.py` 每次 `invoke` 会把 `language / provider / run_mode / thread_id` 挂到 trace 的 `metadata` 上（`_make_config()`），因此能在 LangSmith 里按语言、供应商、运行模式过滤与聚合，直接回答「哪个模型跑得慢、费钱、失败率高」。

### 自建 Grafana 全家桶 —— 基础设施层

CLI 进程跑完即退，Prometheus 拉取抓不到短命进程，所以指标走 **push 模型**：`metrics.py` 在 run/resume 结束时把终态指标推给 Pushgateway，Prometheus 再从 Pushgateway 抓取。

```bash
# 装上报依赖
pip install -e ".[monitoring]"
# 起全家桶（Prometheus + Pushgateway + Grafana）
docker compose -f monitoring/docker-compose.yml up -d
```

`.env` 打开上报：

```dotenv
CODING_AGENT_METRICS_PUSHGATEWAY=http://localhost:9092
```

上报指标（标签 `language / run_mode / instance / run`）：

| 指标 | 含义 |
| --- | --- |
| `coding_agent_run_status` | 本次运行终态（1=done，0=其他） |
| `coding_agent_run_duration_seconds` | 本次运行耗时（秒） |
| `coding_agent_tasks_total` | 本次运行任务总数 |
| `coding_agent_tasks_{done,failed,blocked,pending}` | 各状态任务数 |

Grafana `http://localhost:3000`（admin/admin）加 Prometheus 数据源（`http://prometheus:9090`）即可查询。端口约定、Milvus 指标抓取、Pushgateway 清理详见 `monitoring/README.md`。上报全程降级：未装 `prometheus-client`、或 Pushgateway 不可达时静默跳过，绝不拖垮主流程。

## CI/CD（GitHub Actions）

`.github/workflows/ci.yml` 在 push / PR 时自动跑：

| job | 做什么 |
| --- | --- |
| lint | `ruff check` + `ruff format --check` + `mypy`（mypy 建议性、不阻塞） |
| test | `pytest`，Python 3.11 / 3.12 矩阵，离线、零密钥 |
| build | `python -m build` 打 wheel，装完冒烟 `coding-agent --help` |
| test-docker | Docker 沙箱集成测试（`pytest -m docker` + 全流程 mock），自动运行 |

测试强制 mock + subprocess、离线可跑，故 CI 无需任何 API key / Docker / Milvus。发 PyPI、在 CI 里开 LangSmith trace 等需要密钥的场景，用 GitHub Secrets，永不进仓库。
