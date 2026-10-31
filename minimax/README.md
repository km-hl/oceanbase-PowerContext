# 规有所依：MiniMax × SDD 工程上下文交付物

此目录是 OceanBase 生态赛题 **“规有所依，面向软件持续演进的 SDD 上下文管理系统”** 的独立可运行实现，基于现有 PowerContext 项目，不修改 `2026-competition` 分支。

## 核心闭环

`有效规格 → 当前代码/测试扫描 → OceanBase/seekdb 或离线 SQLite 索引 → 按任务检索上下文 → MiniMax 生成统一 diff → 限定路径下的 git apply → 新功能/回归双测试 → 结果与输入指纹持久保存 → 新进程恢复`。

**已实测**：本地 SQLite 离线链路；修改前新增功能测试 6/6 失败、原回归 3/3 通过；修改后业务测试 9/9 通过；上下文模块测试 7/7 通过。运行记录见 `docs/runs/offline-validation.json`。

**未实测，不能作为已完成证据**：真实 MiniMax API 响应、真实 OceanBase/seekdb 连接，以及 PowerContext Server 的跨会话 Handoff。这三项有真实代码接入/配置或文档，但需有模型 Token、数据库和服务后才能做端到端验证。`sqlite-offline` 结果绝不能冒充 seekdb。

## 提交结构

- `ctxsdd/`：规格版本选择、代码 AST 分块、SQLite/seekdb 存储、按任务检索、带路径/行号/SHA 的 context pack、MiniMax 调用、补丁安全校验、实际测试和状态持久化。
- `business/reading_list/`：真实 Python 业务项目，增加 `status` + `min_rating` 筛选和 HTTP WSGI API。
- `business/tests/`：6 项新增功能测试、3 项原功能回归测试。
- `.sdd/specs/`：同 ID 的 R1 失效规格、R2 当前生效规格，支持内容修改与指纹失效检测。
- `tasks/TASK-001.json`：开发任务、目标路径和验收边界。
- `docs/snapshots/{before,after}/`、`docs/snapshots/applied.diff`：改前、改后源码快照和可应用的统一差异。
- `docs/verify-sample.md`、`docs/runs/`：复现实验与真实离线运行输出。
- `TECHNICAL_NOTE.md`：架构、证据可信边界、限制说明。

## 离线快速验证（无需 Token、数据库服务）

Linux/macOS：

```bash
cd minimax
python3 -m ctxsdd init
python3 -m ctxsdd plan --task '阅读清单 status min_rating 筛选' > /tmp/minimax-context.json
python3 -m unittest discover -s tests -v
python3 -m ctxsdd verify
python3 -m ctxsdd state
bash scripts/e2e_flow.sh
```

Windows PowerShell（需要 Python 3.11+）：

```powershell
cd minimax
python -m ctxsdd init
python -m ctxsdd plan --task "阅读清单 status min_rating 筛选"
python -m unittest discover -s tests -v
python -m ctxsdd verify
python -m ctxsdd state
```

`state` 是**新 Python 进程**读取之前写入数据库的事件记录；变更规格文件或业务源码后，会返回失效路径和 `fresh=false`。自测产生的数据仅写入 `minimax/records/`，该目录不入库。

## 接入真实 OceanBase / seekdb

```bash
pip install "PyMySQL>=1.1,<2"
export MM_OB_HOST=127.0.0.1
export MM_OB_PORT=2881
export MM_OB_USER='root@sys'
export MM_OB_PASSWORD='your-database-password'
export MM_OB_DATABASE=mm_sdd
python3 -m ctxsdd init
python3 -m ctxsdd plan
```

设置 `MM_OB_HOST` 后，存储层使用 PyMySQL 的真实 SQL DDL/DML，将分块落到指定数据库的 `mm_documents` 表，并使用持久事件表 `mm_events`。未配置则默认离线 SQLite。当前小型案例采用全量 SQL 读出后做确定性词汇排序，尚未实现 seekdb 专用的 NGRAM 全文索引；因此不声称完成大型仓库的检索性能优化。

## 接入 MiniMax Code / API

从官方 MiniMax 平台获取可用的 API Key 后，在终端环境变量设置：

```bash
export MM_MINIMAX_API_KEY='your-api-key'
export MM_MINIMAX_MODEL='MiniMax-M3.1-Flash'
python3 -m ctxsdd ask
# 只审查并应用由模型生成的补丁；不会自动执行
python3 -m ctxsdd apply --patch records/model.patch
python3 -m ctxsdd verify
python3 -m ctxsdd state
```

MiniMax 请求经 HTTPS 调用 OpenAI 兼容接口；缺少 API Key 时明确报错，**不会把本地差异冒充 AI 生成**。补丁仅允许修改 `minimax/business/reading_list/` 的两个业务文件，使用 `git apply --check` 先校验，再实际应用。密钥永不入库。

原有的 `integrations/minimax/plugins/powercontext/` Skill 保持可用，SDD 模块提供其工程上下文与验证来源。需使用 PowerContext 的 Memory/Handoff 持久化能力时，在已经连接 PowerContext MCP Server 的 MiniMax Code 会话里，把本次 `context_pack.json`、验证记录的路径及哈希作为交接证据提交；本目录的本地事件表不是 PowerContext Server 的替代品。
