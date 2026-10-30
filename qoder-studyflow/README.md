# StudyFlow · 研习台

面向学习、科研和个人项目的工作台：管理任务、记录研究结论，并用 **OceanBase PowerContext** 保存可跨会话召回的长期记忆。

> 赛题状态：仓库代码已准备。实际使用 Qoder 进行开发、Qoder Sites 发布公网作品，仍须在已登录的 Qoder 账号内完成。不可用 GitHub Pages 或普通网站截图替代。

## 功能

- **学习计划**：任务创建、完成、优先级、截止日期、完成率。
- **研究笔记**：创建笔记，服务端调用 PowerContext 的 /v1/sources/content 采集证据。
- **长期记忆**：只有用户点击“存为长期记忆”才调用 /v1/memory/remember，避免把草稿当作经过确认的结论。
- **历史检索**：调用 /v1/memory/search 查询可跨会话复用的决策。
- **上下文准备**：调用 /v1/context/prepare，为下一次 AI 对话形成有界历史文本。
- **安全边界**：PowerContext token 仅放在 Node 服务环境变量中；用户端不接触 Token。Scope ID 保存在服务器数据目录。

## 本地启动

Node.js 20+，不需要第三方依赖：

    cd qoder-studyflow
    node --test test/server.test.mjs
    node server.mjs

浏览器访问 http://127.0.0.1:4173 。

按主仓库 README_CN.md 启动 PowerContext（默认 127.0.0.1:8000），其最小 SQLite 模式不需要模型凭据。若服务地址不同，配置：

    export POWERCONTEXT_URL=http://127.0.0.1:8000
    export POWERCONTEXT_TOKEN=YOUR_PRIVATE_TOKEN
    # 如果已有 Scope，可额外设置 POWERCONTEXT_SCOPE
    node server.mjs

PowerShell 中对应写法：

    $env:POWERCONTEXT_URL = "http://127.0.0.1:8000"
    $env:POWERCONTEXT_TOKEN = "YOUR_PRIVATE_TOKEN"
    node server.mjs

未启用 PowerContext 鉴权时，不需要设置 POWERCONTEXT_TOKEN。初次需要记忆服务时应用会自动创建 Scope，并在 .data/studyflow.json 持久化 Scope ID。未连接 PowerContext 时本地任务和笔记仍能使用，但记忆操作返回明确的连接错误，不会假装成功。

默认监听 127.0.0.1:4173，仅适合本地开发。**不要直接暴露本示例到公网**：此版是单用户本地模式，公网上线需要由 Qoder 补充登录验证、站点后端和安全的 Secret 管理。不能将 .data 或 token 提交到仓库。

## 演示验收

1. 在任务板新增“复现 HNSW 索引构建”并勾选一个任务，完成率变化。
2. 新增“HNSW 参数结论”笔记，内容包括“HNSW M=16 efConstruction=200”，看到已写入 Source 的状态。
3. 点击“存为长期记忆”，实际触发 PowerContext Memory Remember。
4. 在智能记忆里搜索 **HNSW**，返回保存过的条目；使用 Context Prepare 生成并复制上下文。
5. 重启应用后检查任务、笔记和 Scope 是否保持。打开 PowerContext 端口与服务日志确认真实调用。

测试命令：node --test test/server.test.mjs 。测试使用 mock HTTP PowerContext 验证集成调用、任务 CRUD 和隔离的 Token；**mock 结果不是 PowerContext 实例真实联调的证据。**

## API

| Method | Route | Behavior |
|---|---|---|
| GET | /api/status | 后端和 PowerContext 健康检查 |
| GET | /api/state | 当前数据 |
| POST | /api/tasks | 新增任务 |
| PATCH/DELETE | /api/tasks/:id | 更新或删除任务 |
| POST | /api/notes | 新增笔记并采集 Source |
| POST | /api/notes/remember | 显式保存 Memory |
| POST | /api/search | PowerContext Memory Search |
| POST | /api/context | PowerContext Context Prepare |

接口契约以主仓库 docs/zh/docs/develop/api-quickstart.md、openapi/powercontext.yaml 为准。服务端没有把历史 Context 当成当前指令。

## Qoder Sites 提交

详见 QODER_SITES.md。Qoder Sites Free 套餐只支持静态发布，无法原样运行本站的 Node API。需用支持数据库/Server Functions 的套餐，或经安全认证的后端代理供 Qoder Sites 访问 PowerContext。

请在实际完成 Qoder 编程和 Qoder Sites 正式发布后提交：

- 代码仓库：https://github.com/km-hl/oceanbase-PowerContext
- Branch：qoder
- Commit ID：查看 qoder 分支最新的完整 Commit SHA
- 作品链接：Qoder Sites 公网 HTTPS 地址（不是 GitHub 地址）
- 证据：Qoder 开发对话/提交记录、真实 PowerContext 读写日志、站点公开设置及网站实际操作截图

独立子项目位于 qoder-studyflow，未改动上游 PowerContext Python 核心。