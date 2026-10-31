# 技术使用记录状态

此目录中的 `offline-validation.json` 是真实的**离线**执行日志：它不能证明曾调用 MiniMax API、OceanBase/seekdb 或 PowerContext SDK。

真实在线演示请运行 `bash scripts/online_e2e.sh`，成功后整理并脱敏 `records/online-*/`，把以下实际文件复制到本目录提交新的 Commit：

- `oceanbase-import.json` 与 `context-before.json`（记录真实数据库后端、实际检索条目）
- `minimax-response.json`、真实 `model.patch` 摘要和 `patch-applied.json`（与最终代码 diff 对应）
- `before-feature.log`、`before-regression.log`、`verification.json`（确保原目标失败非环境异常）
- `powercontext-restarted.json` 和 `context-resumed.json`（独立进程恢复状态）

仅保留参数化 SQL 语义和必要标识，不提交 API Key、DB 密码、Bearer Token 或私有推理内容。若在线流程失败，保留失败原因并修复，不得把模板伪装为成功日志。
