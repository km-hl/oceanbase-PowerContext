# 一页技术说明：规格有版本，代码有证据，测试有保质期

## 1. 问题与结构

原实现只有 SDK 内部的 SDD 规则读取，没有赛题规定的独立业务案例、测试前后对比、模型补丁流程及可恢复状态。新增 `minimax/` 独立实现并复用已提交的 PowerContext MiniMax Skill 工作流，避免改动别的比赛题目。

```
.sdd/specs/*.json + business/*.py
          ↓ 版本过滤 + Python AST 切分 + 源文件 SHA-256
    ctxsdd/core.py → mm_documents（OceanBase/seekdb 或 SQLite 离线）
          ↓ SQL 持久查询 + 确定性关键词排序
    context_pack.json （spec 修订/路径/行号/代码符号/候选测试）
          ↓ ctxsdd/ask （真实 MiniMax HTTPS API，需要 Key）
    model.patch → git apply --check → git apply
          ↓ ctxsdd/verify
   feature + regression 双测试 → mm_events 持久事件
          ↓ ctxsdd/state（新进程重建任务进展，核对最新输入指纹）
```

## 2. 状态与一致性

- 规格按 `(id, revision)` 选最大修订；仅 `active` 才生效，R1 已被 R2 取代。
- `plan` 每次重读原文件并刷新数据库索引，因此规格内容修改后，即便 revision 没变，其 `sha256` 和输出也会变。
- 测试记录保留业务源码、测试文件、规格内容的 SHA-256；`state` 重新扫描实时字节，只要一个已声明输入变化就报告 `fresh=false`，不继承历史“通过”结论。
- AST 分块提供 `path`、`line_from`、`line_to`、`symbol` 和文件 `sha256`；未覆盖动态调用图，对这种情况不伪造调用关系。
- `ask` 不替用户执行不可信补丁；`apply` 做路径白名单与 `git apply --check`，并确认文件字节真的改变。

## 3. 可复现验证

快照 `before/service.py` 仅有不带参数的 `list_books()`：新增特性测试 **6 错误**，回归 **3 通过**。实际新版本支持 `status`、`min_rating` 及其 AND 组合，且非法参数拒绝：新增功能 **6 通过**，回归 **3 通过**。工程上下文测试 **7 通过**。详见 `docs/runs/offline-validation.json`。

## 4. 边界

已验证的是本地 SQLite 离线链路，**不是**真实 seekdb、MiniMax 或 PowerContext Server 运行。SQLite 与 seekdb 只是相同模块的两种持久化适配器，后者须配置真实服务并单独留运行证据。真实模型生成的补丁必须以真实 API 返回为准，不能使用人工编写的 `applied.diff` 冒充。后台判题的 `执行失败 0.000` 也可能来自评测器拉取或初始化，无法凭分数判断代码是否被执行。
