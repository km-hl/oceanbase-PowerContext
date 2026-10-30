# 比赛功能与本地验收

实现对应 `question.md` 与 `competition/challenges/*/instruction.md` 的公开接口。比赛按题目整体计分，本地公开测试通过不能换算为平台得分。

## 功能入口

| 赛题 | 入口 | 行为 |
| --- | --- | --- |
| 二：CSV | `powercontext.sources.csv_rows` | 按文档和主键采集，保留原始字段、列顺序与精确来源引用。 |
| 三：Pi | `powercontext.sources.pi_session` | 完整解析 Pi v3 所选分支，保留工具原文；通过 `ConnectorLifecycle` 持久化去重与历史校验进度。 |
| 四：PDF | `powercontext.sources.pdf_pages` | 解析全部物理页，以文档 ID、完整文件摘要和页码建立修订身份；通过 scoped Memory 检索真实页面引用。 |
| 五：HTTP 重排 | `powercontext.builtin.artifacts.memory.http_reranker.HTTPMemoryReranker` | 按批次上限发送全局 rank，严格校验响应，跨批次稳定排序并报告实际请求数。 |
| 六：增量上下文 | `powercontext.builtin.runtime.context_pack`；`runtime.context.for_scope(...).prepare_incremental(...)` | UTF-8 有界分块、标准 Base64、规范 JSON、完整摘要与字节数校验，支持独立接收端重建。 |
| 七：数量触发 | `RuntimeConfig.memory_source_count_threshold`、`topic_memory_source_count_threshold` | 严格正整数，默认关闭；按成功游标后的 journal 数量触发，与时间条件为 OR，保留原生窗口和失败重试。 |
| 八：Embedding | `powercontext.builtin.inference.coalescing.CoalescingEmbeddingModel` | 合并重复、并发与晚到请求，隔离调用取消，限制底层批次并以 LRU 缓存成功向量；按等待过的不同批次汇总 usage。 |
| 九：生命周期 | `runtime.lifecycle.for_scope(...)` | 持久化 all/any 依赖图；在同一数据库事务内提交事件与原生 Memory 状态；Topic 搜索排除不可用节点，保留历史引用。 |
| 十：证据片段 | `PrepareContextRequest(evidence_selection=True)` | 按查询选择原文句子或有界词窗口，返回精确字符区间、scope 与版本引用，完整输出满足 UTF-8 字节预算。HTTP 请求也支持此开关。 |
| 十一：混合检索 | seekdb 原生 `Memory.search(mode="hybrid")` | 一条候选 SQL 分别获取有界全文与向量候选，再沿用既有融合；保持数据库过滤和当前 head 引用。 |

PDF 解析需要可选依赖：

```powershell
uv sync --locked --extra pdf
```

从源码开发使用 `uv sync --locked`，开发依赖已包含 PDF 支持。核心包不会在导入时加载 `pypdf`。

## 运行公开自测

Linux x86_64、Python 3.12 的标准流程见 [`competition/README.md`](../../competition/README.md)。在 Windows 上可使用 Docker Desktop 的 Linux 引擎：

```powershell
uv run python scripts/run_competition_selftests.py --build
```

复用已有镜像并选择题目：

```powershell
uv run python scripts/run_competition_selftests.py --task pi --task lifecycle
```

运行器从当前 `HEAD` 导出公开测试和样例的原始 Git 字节，避免 `core.autocrlf` 改变 JSONL 的 SHA-256。它只在临时容器中规范启动脚本行尾；测试、样例和产品源码只读挂载。测试使用官方锁定依赖，关闭外部网络，限制为 1 CPU、2 GiB，各题独立运行。产品实现加载当前工作区的 `src/`，无需先提交实现；公开测试与样例取当前提交版本。

## 验证范围

本地验收结果：

| 检查 | 结果 |
| --- | --- |
| 官方 Linux 容器中的十道代码题 | 95 项公开测试通过，无跳过。 |
| API 契约、JavaScript 操作与新增边界回归 | 55 项通过。 |
| Supervisor 关闭、worker 清理与调度回归 | 27 项通过。 |
| Topic Memory 持久化及完整性 | 62 项通过，27 项因后端环境条件跳过。 |
| OpenAPI 生成一致性、依赖锁、改动文件 Ruff 检查 | 通过。 |
| 源码、自测运行器与新增测试的类型检查 | 按 Linux 目标平台通过。 |

公开验收覆盖 CSV、Pi、完整 503 页 PDF、HTTP 重排、增量协议、真实子进程数量触发、Embedding 请求预算、来源生命周期、证据片段与真实嵌入式 seekdb。额外回归测试覆盖晚到请求的取消隔离、独立失败批次、真实 SQLite 状态写入故障的原子回滚、入队语句执行期间关闭后释放原生锁，以及长句后部证据的预算选择。

正式评测另有公开包没有提供的完整语料、标注和测量序列，包括 PDF 页面检索召回率、SciFact 证据质量、40 轮增量净传输节省，以及混合检索逐查询质量与延迟。这些门槛需要平台实际验收，不能从公开样例通过推定达标。

生命周期图的处理成本为一个 scope 的节点和边遍历，原生 Memory 状态按 artifact 分组写入；题面规模为每个 scope 16–20 个节点。数量触发按有界 dirty-intent 页读取 journal/cursor 元数据，不扫描 Source 正文。

PDF 检索结合原生全文搜索与页级 BM25 排序，统一词形，并为财务报表问题补充通用查询概念。首次查询及 Memory head 改变时，通过公开 `Memory.list()` 读取当前条目并重建派生页索引；同一 scoped Memory 对象且 head 不变时复用缓存，最多保留四个活跃对象的索引。建索引需要扫描当前 Memory 正文，缓存空间随全文词项数增长；每个问题仍需对页索引排序及回查返回引用。这是召回质量与读取成本的取舍，不是增量索引或恒定成本检索。

证据选择对完整句子窗口和分散的原文句子生成候选，通过包含引用及 envelope 开销的字节预算组合片段。共享英文词形归一化仅用于排序，持久化正文、返回原文和字符坐标不变。默认关闭时沿用普通 prepare。

扩展质量检查的数据范围、命令、结果及限制见[检索质量检查](competition-retrieval-quality.md)。
