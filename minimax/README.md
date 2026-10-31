# 规有所依 — MiniMax × OceanBase × PowerContext SDD 上下文管理

> OceanBase 2026 生态赛题 **minimax/** 独立交付目录。基线来自官方 `2026-competition-ecosystem` 目录树；本仓库的同名分支为重新构建的对应快照。**此代码库不含任何已伪造的模型或数据库在线调用记录。**

## 验收全流程

| 必做项 | 入口 / 可复现证据 | 状态 |
|---|---|---|
| 业务任务有真实修改 | `business/reading_list/`；`docs/snapshots/{before,after}`；`docs/snapshots/applied.diff` | 本地验证 |
| 导入、检索规格/代码/测试 | `python -m ctxsdd init` 和 `plan`；`scripts/spec_update_check.sh` | SQLite 离线验证，OceanBase 需在线运行 |
| MiniMax 真正生成代码差异 | `python -m ctxsdd ask` → `records/model.patch` → `apply` | **未调用真实 API；需用户提供 Key 并执行在线流程** |
| 新增功能与回归测试 | 同一组测试：修改前 6 失败、3 回归通过；修改后 9 通过 | 本地验证 |
| PowerContext 持久状态、重启复用 | `MM_PC_ENABLED=1`，实际 `open_builtin_contexts` / ContentCapture / Source journal；`pc-state`、下一次 `plan.progress` | 适配代码已接入，需安装 SDK 后在线运行确认 |
| 完整提交材料 | 本目录源码、文档、快照、样例、离线记录、待生成的在线日志位置 | 在线记录尚待生成 |

**重要：本地 `sqlite-offline` 模式只用于自测，不能作为 OceanBase、MiniMax、PowerContext 三项真实使用的证明。** 题目是六项全部通过才得分；不能仅用本地测试成绩断言获得 20 分。

## 业务项目与任务

这是一个不需要额外服务的 Python 阅读清单业务：`ReadingList` 提供 `list_books(status=None, min_rating=None)` 及 WSGI GET `/books`。任务 `tasks/TASK-001.json` 要求：增加 `status`、`min_rating` 及组合过滤，同时保留原来添加书籍、去重、无过滤结果顺序和非法输入校验等行为。

- `.sdd/specs/reading-list-r1.json` 是历史规格；`r2.json` 为当前有效规格。
- `business/tests/test_feature.py` 六项新增行为测试，`test_regression.py` 三项原有回归测试。
- `docs/snapshots/before/service.py` 是缺少筛选功能的真实旧代码，`after/service.py` 是实现后的代码，`applied.diff` 是人工参考差异，**不能冒充 MiniMax 生成结果**。

## 安装（干净 Python 3.11+ 环境）

```bash
cd minimax
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

`requirements.txt` 明确包含 `powercontext[builtin]>=1.2,<2` 和 `PyMySQL>=1.1,<2`。在线模式另需要网络以及真实运行的 OceanBase/seekdb 服务、MiniMax 模型 Key。实际最终依赖版本见安装环境 `pip freeze` 输出。Windows 用对应 PowerShell 的 venv 激活方式。

## 无密钥离线回归（可立刻复现）

```bash
python -m unittest discover -s tests -v
python -m ctxsdd init
python -m ctxsdd plan --task 'status min_rating 筛选' > records/context-pack.json
python -m ctxsdd verify
python -m ctxsdd state
bash scripts/e2e_flow.sh
bash scripts/spec_update_check.sh
```

`plan` 每次按有效规格扫描真实文件，按 AST 分块后写入 `mm_documents` 并从 SQL 表**实际读取**检索结果，输出 JSON 包含 `specifications`、`inactive_specs`、`evidence[kind,path,line_from,line_to,symbol,sha256]`、`verification`、`progress`。若修改有效规格正文而不升级版本，SHA 与再次检索内容也会变化。脱离实际仓库文件或虚构条目的输出不会出现。

本地 `state` 是用于离线验证的 `mm_events`，**不算 PowerContext 的证明**；只有开启 `MM_PC_ENABLED=1` 后才会使用真正的 PowerContext Source journal。

## 真实 MiniMax + OceanBase + PowerContext 全流程（评审所需）

不要将真实 Key 提交到 Git。准备一个可访问的 seekdb/OceanBase MySQL 服务（示例端口 2881），在当前终端设置下列变量：

```bash
export MM_MINIMAX_API_KEY='<your MiniMax API key>'
export MM_MINIMAX_MODEL='MiniMax-M3.1-Flash'
export MM_OB_HOST='127.0.0.1'
export MM_OB_PORT='2881'
export MM_OB_USER='root@sys'
export MM_OB_PASSWORD='<your database password>'
export MM_OB_DATABASE='mm_sdd'
export MM_PC_ENABLED=1
export MM_PC_SCOPE='minimax-sdd-reading-list-task'
```

然后在**干净 worktree** 运行：

```bash
bash scripts/online_e2e.sh
```

脚本依次执行：还原功能未实现的源码 → 验证新增测试失败且旧回归通过 → **seekdb/OceanBase 真建表和导入文档** → 按任务从数据库检索规格/符号/测试 → **MiniMax API 真返回补丁** → 审核允许的路径并应用 → 跑新增功能与回归测试 → **通过 PowerContext SDK 将验证结果保存为 Source** → 新 Python 进程 `pc-state` 读 Source journal → 新 `plan` 从 PowerContext 恢复 `progress`。所有结果写入被 Git 忽略的 `records/online-时间戳/`；生成后应检查脱敏，再提交到 `docs/runs/` 作为官方技术使用记录。

注意：本脚本只接受真实调用与真实测试，不会“预先生成”API 回执；模型可能生成无法应用或不能通过测试的补丁，此时脚本非零退出，属于需修复的真实失败。若要确认数据库种类，请在真实服务执行 `SELECT VERSION(), @@version_comment;` 并保留响应及 SQL 检索记录。

### 分步调试

```bash
python -m ctxsdd init           # SQL 建库/建表、重建索引
python -m ctxsdd doctor         # 类型/数据量/是否有模型 Key
python -m ctxsdd plan           # 返回上下文（会读历史 PC 进展）
python -m ctxsdd ask            # 真实 MiniMax HTTPS 模型调用
python -m ctxsdd apply --patch records/model.patch  # git apply --check，然后实际应用
python -m ctxsdd verify         # 功能与回归分别记录退出码并保存 PC Source
python -m ctxsdd pc-state       # 独立进程重新打开 PowerContext SQLite Source journal
python -m ctxsdd plan           # 进展保存在输出 progress 字段中
```

PowerContext 使用 `BuiltinConfig(database=SQLiteConfig(...))` / `open_builtin_contexts`，Scope 通过固定 `idempotency_key` 恢复；内容由 `ContentCapture` 写入 Source journal，后续进程从 `journal.entries()` 读取并折叠。这是真正的 PowerContext SDK 写入与读取，不需要搭建 PowerContext HTTP Server；**不要把额外自建的 SQLite 事件表当成 PowerContext**。

## 文件说明与已知限制

- `ctxsdd/core.py`：有效版本筛选、源码和测试定位、OceanBase/SQLite 适配、基于查询相关性的文档选取、SHA 新鲜度。
- `ctxsdd/powercontext_state.py`：PowerContext SDK 原生来源存储与恢复。
- `ctxsdd/__main__.py`：MiniMax 请求、路径白名单补丁、实际测试、CLI。
- `scripts/online_e2e.sh`：真实在线闭环（需要 Key 和数据库），`scripts/spec_update_check.sh`：真实规格刷新。
- `docs/verify-sample.md`：验证样例与结果；`TECHNICAL_NOTE.md`：一页设计/技术分工；`docs/runs/offline-validation.json`：离线测试日志。

本例 SQL 查询确实从 OceanBase 返回持久文档，但关键词相关性在 Python 侧排序，没有声称使用 NGRAM、向量检索或大规模代码图。Python AST 只处理显式顶层函数与类，动态调用和跨语言工程未覆盖。MiniMax 补丁不应未经审查在生产工程应用。必须运行并提交真实在线记录才能主张全部必做项满足。
