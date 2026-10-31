# 一页技术说明：规有所依的工程上下文

**问题与交付**：AI 编程助手在长期维护中容易引用过期规格、错过回归边界或误信旧测试。此系统提供一个真实可执行的阅读清单业务案例（`status`、`min_rating` 两项局部筛选），用不可混淆的规格版本与当前源码哈希组织工程证据；模型改码和验证是独立、可失败的实际步骤。

## 架构与职责

```
.sdd/specs/*.json ─┐
业务代码 + 原测试 ───┤ AST / JSON 分块 + 文件 SHA-256
                   ▼
             ctxsdd/core.py
                   │ SQL 导入/检索
                   ▼
           OceanBase/seekdb (mm_documents)
                   │ 查询结果含 path/lines/symbol/source hash
                   ▼
           context-pack.json + PowerContext 旧任务进度
                   │ MiniMax HTTPS API (真实模型生成 diff)
                   ▼
       model.patch → 白名单 + git apply --check
                   ▼
       实际修改的源码 + 目标功能测试 + 原回归测试
                   │ 状态与验证证据
                   ▼
     PowerContext Builtin Source journal / SQLiteConfig
                   │ 新进程 pc-state + 再次 plan(progress)
                   ▼
               延续未完成任务
```

**数据选取**：只使用最高且 active 的版本规格；r1 被 r2 替代。代码经 Python AST 获取顶层符号和行号，测试按规格关联路径定位。`plan` 每次重导入持久 SQL 行并查询，关键词得分采用包含匹配/子词二元组后进程内排序。这个简单策略保证可复现，但不声称全文索引或向量相关性优化。文件、规格内容 SHA 均附在结果中，旧验证输入发生变化即失效。

**MiniMax**：API Key 来自环境，仅 HTTPS 请求；请求以当前 SQL 结果和旧状态构造，不附加私有思维链。返回代码补丁保存本地，记录模型标识、响应 ID、Token 计数、补丁摘要。只允许修改指定业务源码，经 `git apply --check` 实际变更后才保存进展；人工参考 diff 与真实 API 回执严格区分。

**OceanBase/seekdb**：`MM_OB_HOST` 非空时走 PyMySQL，创建/写入 `mm_documents`，从持久 SQL 结果构造上下文；离线只用于单元测试的 SQLite 不可冒充该项。数据库地址、版本和实际查询需在在线运行中确认。

**PowerContext**：`MM_PC_ENABLED=1` 使用原生 `BuiltinConfig` + `open_builtin_contexts`，固定 idempotency Scope，保存 `ContentCapture` 来源条目，通过 `journal.entries()` 在新进程读取，并将之前的验证和待办写入下一次 `plan.progress`；自有的 `mm_events` 只作无 SDK 离线测试及日志。

**评审证据**：同一组 `test_feature.py` 在旧版源码 6 项失败，`test_regression.py` 原回归 3 项通过；新版全部 9 项通过（离线实测）。`scripts/spec_update_check.sh` 检查修改规格后重新导入且 context 改变。PowerContext SDK 跨进程验证已由 GitHub Actions 完成；真实 MiniMax API 与 seekdb 服务的运行证据仍需分别核对，不能从本地 SQLite 结果推断完成。

**限制**：Python 顶层 AST 覆盖、全表 SQL 读取加本机排序不适合超大型仓库；只允许单任务局部补丁；模型输出可能不能应用或需要重试。不会自动执行来源文件中的命令，也不会把私密 Token 写入仓库。
