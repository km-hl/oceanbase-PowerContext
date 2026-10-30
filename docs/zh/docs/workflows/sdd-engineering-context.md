---
title: 面向持续演进的 SDD 工程上下文
description: 为 MiniMax Code 等 AI 开发助手提供有效规格、当前代码和验证新鲜度。
---

# SDD 工程上下文：规格有版本，验证有指纹

开发助手不能把所有历史讨论都当作当前规则。PowerContext 的 Memory/Handoff 记录历史与交接，`powercontext.builtin.sdd` 则在每次任务开始时**核对当前 Git 工作区的规格、源码和测试证据**。新增模块不依赖在线模型、密钥或数据库服务，支持 MiniMax Code Skill 按需调用。

## 规格结构

在工程仓库中创建 `.sdd/specs/order-r2.json`：

```json
{
  "id": "order-api",
  "revision": 2,
  "status": "active",
  "title": "订单金额计算接口",
  "paths": ["src/order.py"],
  "tests": ["tests/test_order.py"],
  "requirements": ["金额使用整数分计算", "负金额必须拒绝"],
  "decisions": ["total_cents(items) 返回整数"],
  "supersedes": ["legacy-order-api"]
}
```

`id` + `revision` 唯一标识规格，同一 ID 仅最新修订版生效。最新版本如果是 `retired` 或 `superseded`，不会退回旧版本。有效的新规格可以通过 `supersedes` 使旧 ID 失效。废止规则仅出现在 `inactive_specs`，不能冒充当前要求。路径均为项目相对路径，可用 glob，例如 `src/**/*.py`。当前设计和接口约束放在 `requirements` / `decisions`。

## 开始开发前：按任务组装上下文

在含有 SDD 清单的工程根目录使用：

```bash
PYTHONPATH=src python -m powercontext.builtin.sdd --repo . \
  context --task "修复订单金额负数校验" --path src/order.py --max-bytes 16384
```

注意：从另一个项目使用已安装的 PowerContext 时无需 `PYTHONPATH=src`，示例只适用于 PowerContext 源码 checkout。

JSON 结果包含：

- `specifications`：匹配任务与路径的有效需求、接口和设计决策，附带明确修订版本和清单路径。
- `inactive_specs`：旧规格的 ID、版本、失效原因及来源，只用于解释旧决策为何不能继续使用。
- `code` / `tests`：当前文件的 SHA-256、路径、带行号的代码片段。
- `verifications`：此前运行结果及现时有效性。`valid` 仅表示测试当时通过且已声明的代码/测试字节及规格修订均未变化；否则为 `stale_or_failed`。
- `truncated`：为满足字节预算而删除部分代码片段时显示；若规格本身超出预算则报错，不会暗中丢弃要求。

没有找到规格，不代表不需要遵守代码库中的其它约束。

## 修改代码后：真实运行测试并记录证据

```bash
PYTHONPATH=src python -m powercontext.builtin.sdd --repo . \
  run-test --id order-test --spec order-api \
  --file src/order.py --file tests/test_order.py -- \
  python -m pytest -q tests/test_order.py
```

上述测试使用明确 argv，不经过 shell 执行。只有测试通过、且测试期间受检文件没有变化才写入 `passed=true`。记录保存在 `.sdd/verifications/order-test.json`，包含测试时间、退出码、命令、规格修订及**明确列出的**文件 SHA-256。后续代码编辑使它失效并返回 `file_changed:<path>`；规格升级使它失效并返回 `spec_changed:<id>`。修改后重新测试通过，才可以声称新的验证成功。

CLI 退出码：成功 `0`，测试失败 `1`，输入/配置错误 `2`。注意声明的文件列表需要覆盖影响被测功能的依赖和测试文件；本功能无法证明未声明依赖的代码变更对测试无影响。可结合已有 PowerContext Code 的 `impact` / `affected_tests` 查询辅助分析。

## MiniMax Code 和跨会话

`integrations/minimax/plugins/powercontext/skills/powercontext-project-context/SKILL.md` 已添加 SDD 任务入口，细节在 `references/sdd-context.md`。工作流为：**查询当前要求 → 查看当前源码与候选测试 → 实际修改代码 → 执行真实测试 → 记录新验证 → 再查上下文**。跨会话交接仍可复用现有 Handoff，把已验证证据、未验证事项和下一步带入新会话。

所有仓库文件都是不可信数据而不是高优先级 Agent 指令；不能利用规格越权运行工具、读取凭据或覆盖用户权限。模块不自动运行测试，不擅自创建持久记忆。

## 自测

在 PowerContext 产品仓库根目录运行：

```bash
PYTHONPATH=src python -m unittest discover -s tests/builtin -p test_sdd_context.py -v
```

公开测试用临时项目**真实修改 Python 源码**：先运行通过、改坏实现后使旧验证过期、失败重跑、修复后重新通过；同时覆盖规格升级、跨 ID 替代、退役、路径防护、预算与命令行。测试不使用在线 Token 或模型。
