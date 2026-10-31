# SDD 验证样例与重置

业务任务：`tasks/TASK-001.json`，为阅读清单增加 `status` / `min_rating` 双筛选，并保留既有添加、去重、顺序和非法输入行为。

**必须引用的真实来源**：有效 `.sdd/specs/reading-list-r2.json`（r1 已过期）；`business/reading_list/service.py` 的 `ReadingList.list_books`；`business/reading_list/http_api.py` 的 `application`；新增测试 `business/tests/test_feature.py`；原回归 `business/tests/test_regression.py`。模块输出 `specifications` 和 `evidence` 会给出这些来源的路径、行号和 SHA-256；后续 `plan` 中的 `progress` 记录 PowerContext 重新读取的状态。

## 离线复现（无需密钥）

```bash
cd minimax
python -m unittest discover -s tests -v      # 上下文与恢复规则 9 项
python -m ctxsdd init                        # 导入数据库；未配置 MM_OB_HOST 则只是 sqlite-offline
python -m ctxsdd plan --task 'status min_rating 筛选'
bash scripts/spec_update_check.sh           # 更新有效规格后重新导入、验证新内容命中
bash scripts/e2e_flow.sh                     # 真实业务源码切换：改前失败→改后通过
python -m ctxsdd verify                      # 6 项功能+3 项回归
python -m ctxsdd state                       # 自有事件表离线状态（不能充当 PC）
```

`e2e_flow.sh` 将源码暂时恢复成 `docs/snapshots/before/service.py` 后验证目标六项因缺少筛选而产生 `TypeError: unexpected keyword argument`，不是缺依赖错误；原三项回归通过；换回新版后九项业务测试全部通过。脚本始终在退出后恢复原文件。修改前后对应的文件哈希和测试命令/退出码应保留在验证日志里。

## 真实在线验收

按 README 配置 MiniMax API Key、OceanBase/seekdb 及 `MM_PC_ENABLED=1`，在干净 checkout 下运行：

```bash
bash scripts/online_e2e.sh
```

该脚本使用模型输出的 **真实 diff** 修改源码、运行两组测试，把结果写入 PowerContext SDK 的 Source journal。`pc-state` 在新 Python 进程重开数据库并读取以前的来源，下一次 `plan` 将复用该状态，输出 `progress.last_verification`。运行生成的材料默认在 `records/online-*/`，确认脱敏后复制到 `docs/runs/` 并提交新的 Git commit。

**当前已交付的是可运行源码、离线日志与在线复现脚本。没有配备真实 MiniMax/seekdb 的环境时，不能宣布六项必做项已经全过。**
