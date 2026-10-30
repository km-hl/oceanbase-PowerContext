# 🪐 FunVerse：平行宇宙人生模拟器

2026 OceanBase「Just for Fun，用 Dify 做一个好玩的智能应用」参赛作品。

这是**真正以 Dify 为 AI 编排层、以 OceanBase seekdb 为持久记忆数据库**的互动故事游戏。每输入一次离谱的选择，Dify 先经由 HTTP 节点查询 seekdb 的历史冒险，再调用 LLM 续写，随后把新剧情写回 seekdb；写入成功后才向玩家显示内容。玩家可以重复打开新的聊天，续接过去的伏笔。没有 SQLite / 内存替代数据库的“伪集成”。

## 实现效果与数据流

    玩家：我在赛博重庆开了一家只卖回忆的火锅店
      ↓
    Dify Chatflow「开始」(sys.user_id / sys.query)
      ↓
    HTTP POST /v1/context  → seekdb 按玩家隔离的最新 3 章 + 相关旧伏笔
      ↓
    Dify LLM 节点 → 生成世界观、反转、A/B/C 三个选项
      ↓
    HTTP POST /v1/save → seekdb 持久化一章 (UNIQUE player_id+turn_id)
      ↓
    Dify「直接回复」→ 玩家继续选择

核心实现文件：

- [Dify 可导入 Chatflow DSL](dify/FunVerse-Chatflow.yml)，包含完整节点、连线、提示词、环境变量。
- [SeekDB HTTP API](api/main.py)，使用 PyMySQL 与 OceanBase seekdb 的 MySQL 协议连接。
- [Docker Compose](compose.yaml)，单独运行 seekdb + API，使用持久化 Volume。
- [自动化测试](tests/test_funverse.py) 和 [真实数据库 smoke 脚本](scripts/smoke.sh)。

## 一、启动 seekdb 与 API

推荐在 Linux VPS 上运行 Docker Compose，数据库容器按官方建议至少预留 1 CPU、2GB 内存；Dify 实例另行部署，容量按模型及 Dify 官方要求规划。

    git clone --branch dify https://github.com/km-hl/oceanbase-PowerContext.git
    cd oceanbase-PowerContext/examples/dify-funverse
    cp .env.example .env
    # 编辑 .env，替换数据库强密码和 FUN_API_TOKEN；不要将 .env 提交到 Git！
    docker compose up -d --build
    docker compose ps
    curl -fsS http://127.0.0.1:8765/healthz

预期健康接口形如：

    {"status":"ok","database":"oceanbase-seekdb","chapters":0}

在本机运行一次写入和读取验证：

    set -a
    . ./.env
    set +a
    bash scripts/smoke.sh

脚本会调用 /v1/save 和 /v1/context，最后进入 seekdb 容器执行 SQL 统计章节数。只有实际返回成功时才能算通过实库验证。数据库数据落在 Docker 命名卷 seekdb-data 中，重建 API 容器不会抹掉历史。

**切忌**将 2881/2886 暴露到公网。本项目只把 API 监听映射到宿主机 127.0.0.1:8765；端口 8765 也不应在没有 Token 鉴权、HTTPS 和网络访问控制时公开。

## 二、部署 Dify 并导入应用

1. 使用已有 Dify 平台，或按照 [Dify 官方 Docker Compose 文档](https://docs.dify.ai/en/getting-started/install-self-hosted/docker-compose) 安装。Dify 本体不包含在本项目 Compose 中。
2. 在 Dify 控制台点击「创建应用 → 导入 DSL」，选择 dify/FunVerse-Chatflow.yml。这是 advanced-chat（Chatflow）应用，DSL 格式版本 0.7.0，建议 Dify 1.16.x 或更新版本；旧版本需先升级或用目标版本重新导出适配。
3. 配置模型服务，例如阿里云通义千问供应商，选择 qwen-plus 并添加自己的 API Key；若使用其他模型，在 LLM 节点手工重选可用模型。**本仓库不包含 LLM 凭证**。
4. 在 Dify 的「环境变量」中设置 FUN_API_BASE_URL 和 FUN_API_TOKEN。后者必须与网关 .env 中的 FUN_API_TOKEN 完全一致，原 DSL 中的 REPLACE_ME 是无效占位符。
5. 点击「预览」，输入一段人生设定。至少互动两轮后，在 seekdb 查看记录，确认检索和持久写入都真实发生，然后发布 Dify WebApp 并保存访问 URL。

**同一台宿主机、Dify 也通过 Docker 运行：** 使用默认 FUN_API_BASE_URL = http://funverse-api:8765，需让 Dify API / Worker 容器加入网关的 Docker 网络。例如先用 docker ps 查看准确名称，再执行：

    docker network connect funverse-network <Dify API 容器名>
    docker network connect funverse-network <Dify Worker 容器名>

把尖括号占位符替换成实际容器名（不需要把这些字符输入终端）。若 Dify 服务还包括多个 worker 副本，每个执行工作流的 worker 都要能访问 funverse-api。容器重建后可能需重新加入网络，更稳定的方式是在 Dify Compose 中声明外部网络。Dify API / worker 所在网络需要能解析 funverse-api。

**Dify Cloud 或异地 Dify：** FUN_API_BASE_URL 必须改成 Dify 服务可访问的 HTTPS 域名，经反向代理转发到本机 8765，并启用证书、最小权限访问控制及 Token。不可在网上直接暴露 seekdb。Dify 的 HTTP 请求节点可能受到 SaaS 版网络/SSRF 策略约束，请实测可达性。

**注意：** Dify WebApp 和 API 调用可能有不同的用户 ID 体系；相同 Dify user_id 才会读到同一玩家的记忆。不要让浏览器任意指定 player_id，网关仅供持有 Token 的 Dify 服务调用。

## 三、现场演示与提测建议

第一轮输入：

    我是一只会编程的熊猫，突然被选为火星市长。

第二轮选择 A 或输入：

    我决定把市政府改建成火锅店。

随后**创建新对话**，尝试：

    还记得那位火星熊猫市长的火锅店吗？我选择召回旧搭档。

因为数据库用 Dify 的 sys.user_id 分区，新会话可以取回同一用户的相关历史。每轮写入有 sys.workflow_run_id 作为 turn_id，重试同一轮不会产生重复行。

验证实际数据库记录（在本项目目录执行，注意不要公开真实玩家输入）：

    docker exec funverse-seekdb sh -c 'mysql -h127.0.0.1 -P2881 -uroot -p"$ROOT_PASSWORD" -D funverse -e "SELECT id, LEFT(choice,50) AS choice, created_at FROM funverse_chapters ORDER BY id DESC LIMIT 10;"'

用于提交或录屏的证据建议包含：Dify 编排画布五个节点与四条边、真实运行的用户故事、Dify HTTP 请求节点的成功响应、seekdb SQL 数据行、跨会话的旧伏笔回忆、Dify 发布后 WebApp 访问页面。不要在截图中展示 Token、密码、API Key 或个人聊天资料。

## 四、本地开发测试与状态边界

    python -m pip install -r requirements.txt "pytest>=8,<10" "httpx>=0.27,<1" "PyYAML>=6,<7"
    python -m pytest -q tests/

以上是**离线契约测试**，覆盖 API 鉴权、按玩家隔离、写入幂等、相关记忆排序和 Dify 编排图结构，测试采用 FakeConnection，不等于实库或真实 Dify 平台集成通过。真正验收必须启动 seekdb，运行 scripts/smoke.sh，再将 DSL 实际导入 Dify 并测试多轮对话。

数据库不可用时 API 返回 503，不会伪装成功或把数据写到本地 JSON / SQLite。Dify 先调用写库节点，最后才进入输出节点，以免数据库写入失败时出现误导性的“已存档”。

## 五、技术边界与安全

- 采用 seekdb 的结构化 SQL 检索和持久化功能（不是向量搜索；赛题只要求实际使用 seekdb 或 PowerContext 至少一种）。
- 读取默认最多最近 40 条章节，再选最新 3 条和最多 2 条与本轮输入相关的旧回忆；限制输入与回忆长度避免无限增长。可根据更大规模需求改用 seekdb 全文/向量索引。
- SQL 完全参数化；网关通过请求头 Token 鉴权；同一玩家/同一运行 ID 的写入幂等；用户 ID 由可信 Dify 服务器提供。
- 本项目是演示应用，不提供注册、计费、审计、限流和对 Token 泄露后伪造身份的完整保护；若面向公众开放，需补齐速率限制、身份验证与数据删除策略。
- 如果要删除测试章节，可连接 seekdb 执行按 player_id 条件的 DELETE，不要清空整个数据库。

项目不改动原有 PowerContext 运行时与测试，仅在 dify 分支增加独立 demo 和对应 CI。
