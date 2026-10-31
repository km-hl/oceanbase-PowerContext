# 复现步骤与预期结果

在独立 checkout 或临时 worktree 内运行，避免覆盖自己的业务代码。所有命令均在 `minimax/` 内执行。

```bash
python3 -m unittest discover -s tests -v  # 7 passed
python3 -m ctxsdd init                    # indexed_chunks > 0, backend sqlite-offline
python3 -m ctxsdd plan --task '阅读清单 status min_rating'
python3 -m ctxsdd verify                  # 6 feature + 3 regression passing
python3 -m ctxsdd state                   # latest_verification.fresh = true
bash scripts/e2e_flow.sh                  # 自动恢复之前文件
```

`e2e_flow.sh` 将真实源码临时替换为 `docs/snapshots/before/service.py`，确认目标测试不通过而旧回归通过，然后恢复改后源码并确认全部通过。脚本使用退出清理钩子恢复文件，**不会**使用预先写死的“测试通过”输出来替代命令执行。

规格新鲜度：编辑 `.sdd/specs/reading-list-r2.json` 的一条 `requirements`，保持 revision 不变，执行 `python3 -m ctxsdd state`，旧验证的 `fresh` 必须变成 `false`，并输出该规格文件的 `invalidated_paths`。恢复原文后可以重新执行 `verify` 建立新记录。

可选在线流程：配置实际 MM_OB_*、MM_MINIMAX_API_KEY 后运行 `init → plan → ask → apply → verify → state`，并独立记录真实连接及模型 API 回执。没有这些凭证时应明确标注未运行，而不是伪造成功。
