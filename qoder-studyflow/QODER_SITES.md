# Qoder 开发 + Qoder Sites 发布指南

本文件是赛题交付流程，不是已经完成的部署记录。**实际 Qoder 生成/编辑和 Sites 发布需要真实操作，不能由 GitHub 提交冒充。**

## 一、在 Qoder 里打开 qoder 分支

1. 登录 Qoder IDE 或 Qoder CLI。仓库地址：https://github.com/km-hl/oceanbase-PowerContext
2. 如果在自己的 Windows 开发机操作，在 PowerShell 执行：

       git clone -b qoder https://github.com/km-hl/oceanbase-PowerContext.git
       cd oceanbase-PowerContext
       git switch qoder

3. Qoder 编程模式打开这个仓库，确认分支是 qoder，不要切回 2026-competition。
4. 在 Qoder Agent 中粘贴下面的开发任务并让它**实际修改文件、运行测试、生成提交**。保存对话界面与 Git commit 作为开发证据。

### 可直接复制到 Qoder 的开发任务

    请继续开发当前仓库 qoder 分支中的 qoder-studyflow，严禁修改 2026-competition 分支。
    这是 OceanBase “用 Qoder 做一个让学习、科研和工作更高效的项目”赛题。
    先阅读 qoder-studyflow/README.md 和 server.mjs，分析已有工作台和 PowerContext API。
    在现有基础上实际完成一项增量功能：给任务增加按项目筛选与逾期高亮，
    补充针对该功能的测试，并运行 node --test test/server.test.mjs；
    不能只做文字讲解，必须真实修改仓库文件并记录 Qoder 操作。
    保留真实 PowerContext Source、Memory、Search、Context Prepare 调用，
    不要替换为假数据；PowerContext 不在线时必须展示连接异常。
    验证完成后在 qoder 分支提交，报告完整 Commit SHA。

这样既体现真正的 Qoder 开发，又能在已有功能基础上进一步提效。

## 二、实机验证 PowerContext

1. 安装并运行主仓库描述的 PowerContext Server。
2. 用浏览器进入 http://127.0.0.1:4173，执行 README 中演示步骤。
3. 检查 Source→Remember→Search→Context 的真实网络调用及结果。
4. 截图留存：PowerContext 服务运行、UI 已同步 Source、Memory 查询命中、Context 返回值。

基础 Memory/Source 路径不需要 LLM/Embedding 凭据。无关查询返回 empty 是正常结果，不能把 empty 伪造成成功命中。

## 三、在 Qoder Sites 上发布

Qoder Sites 文档：https://docs.qoder.cn/qoder/sites
Qoder CLI Sites 文档：https://docs.qoder.com/zh/cli/sites

Qoder Sites 中打开“站点”→“添加站点”，建立 @sites 任务；或在 Qoder CLI 当前项目中请求 Sites Skill 从现有项目构建/发布。

### 可复制到 Qoder Sites 的部署任务

    @sites
    请基于我在 qoder 分支已完成的 qoder-studyflow 项目，建立正式可交互的网站，
    名称为 StudyFlow 研习台，保留当前界面布局与真实交互。
    网站需要支持：任务 CRUD、项目/优先级/日期、研究笔记、主动收藏长期 Memory、
    PowerContext Memory Search 和 Context Prepare。
    注意：此项目原版含 Node.js 服务端 server.mjs，需要迁移到 Qoder Sites
    支持的 Server Functions 或等价安全后端，不能只上传 HTML/CSS/JS 静态文件。
    服务端从 Secret 读取 POWERCONTEXT_URL 和 POWERCONTEXT_TOKEN，
    所有 PowerContext 请求必须由 Server Function 发起；
    不能将任何 token、数据库口令或内部 PowerContext URL 泄漏到网页。
    任务/笔记应保存到站点数据库，并用用户身份或明确隔离的工作空间限制访问；
    不能让所有未登录访客共用并修改一个公开数据空间。
    请实际跑通新建任务→保存 Source→显式 Remember→Search→Prepare 的端到端操作。
    如果套餐不支持 Server Functions、Secrets 或数据库，请直接指出阻塞，
    不要用模拟数据或浏览器本地存储声称已连接 PowerContext。
    先生成预览，并显示真实调用结果；在验证后引导我正式发布。

要满足真实后端能力，Qoder Sites Free 的“仅静态站点”能力并不足够。若 Qoder Sites 对外部 PowerContext 的访问受阻，需先为 PowerContext 部署可通过 HTTPS 访问的可信后端代理，且施加站点身份认证；不应公开无鉴权的 PowerContext。

## 四、公开发布与成绩提测

1. 在 Qoder Sites 预览中演示上述闭环，点击 **发布**。
2. 在分享设置中切换为 **任何人都可访问**（个人版）并确认。实际业务数据不要使用个人敏感信息。
3. 使用无痕窗口访问正式的 Qoder Sites URL，检查页面、任务、笔记、搜索和上下文。
4. 在提交页面填写：
   - 仓库地址：https://github.com/km-hl/oceanbase-PowerContext
   - Commit id：以 Qoder 提交后的 qoder 最新完整 SHA 为准
   - Branch：qoder
   - 作品链接：Qoder Sites 正式公开 HTTPS 链接
5. 保存 Qoder 开发记录、Sites 预览/发布记录、真实 PowerContext 调用日志截图。

不要提前填一个虚构的 Sites 链接，也不要把当前未发布的 localhost、GitHub 代码链接当作“作品链接”。

## 五、验收边界

- 已有源代码及自动化测试：可检验 GitHub 分支及测试文件。
- Qoder 实际使用：必须由你在 Qoder 账号内操作并保留真实记录。
- Qoder Sites 部署：必须登录并实际发布之后，才能得到正式链接。
- PowerContext 真实接入：只有连接真实服务器完成测试，才算已实机验证。
