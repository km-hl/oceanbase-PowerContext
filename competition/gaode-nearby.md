# 周边去哪儿：基于高德的地点推荐助手

这是一个可以在 Agent 中调用，也可以独立运行的轻量 Skill。它不需要 LLM Key 或专用服务器：用一句话表达想去哪里，由高德 Web 服务 API 提供**真实地点、路线和导航链接**。

> 该功能不依赖 seekdb/PowerContext 数据库运行；它是高德 Skill 独立赛题。为避免影响本仓库原有 PowerContext 参赛题，本实现只提交在独立 `gaode` 分支。

## 项目文件

| 文件 | 用途 |
| --- | --- |
| [SKILL.md](../SKILL.md) | 根目录的 Skill 说明，便于赛题评测及直接阅读 |
| [Agent Skill](../.agents/skills/amap-nearby/SKILL.md) | Agent 可发现的 Skill 入口 |
| [scripts/amap_nearby.py](../scripts/amap_nearby.py) | 标准库 Python 实现（地点检索、地理编码、路线规划、导航） |
| [tests/test_amap_nearby.py](../tests/test_amap_nearby.py) | 零外部 Key 的离线单元测试 |

## 使用

1. 到 [高德开放平台控制台](https://console.amap.com/dev/key/app) 新建 Web 服务类型 Key。
2. 配置环境变量（不要加入仓库）：

   - Linux/macOS: `export AMAP_API_KEY="你的Key"`
   - Windows PowerShell: `$env:AMAP_API_KEY = "你的Key"`

3. 在仓库根目录执行：

```bash
python scripts/amap_nearby.py --query "我在北京望京SOHO附近，找2公里内可步行到达的咖啡馆" --format markdown
```

如果一句话中没有明确起点：
```bash
python scripts/amap_nearby.py --query "附近有什么好玩的地方" --location "杭州市西湖区西湖景区" --limit 5
```

如果已经有从高德得到的 GCJ-02 坐标：
```bash
python scripts/amap_nearby.py --query "附近找家书店，想骑车去" --coordinates "116.481499,39.990475" --format json
```

也可使用 `--category`、`--radius`、`--mode walk|bike|drive`、`--city`、`--limit` 定制搜索。

## 数据流

```text
一句话 → 场所/半径/出行方式识别 → 起点验证
               │                     │
               └─────────────────────┘
                         ↓
                高德地理编码（可选）
                         ↓
             高德 POI 2.0 周边搜索
                         ↓
            坐标校验 / 半径过滤 / 去重
                         ↓
             高德步行/骑行/驾车路线
                         ↓
           真实地点 + 可解释理由 + URI 导航
```

- 地理编码：`GET /v3/geocode/geo`
- 周边 POI：`GET /v5/place/around`，请求 `show_fields=business`，返回许可的评分、消费字段。
- 路线规划：`GET /v5/direction/walking|bicycling|driving`，请求 `show_fields=cost`。
- 导航链接：`https://uri.amap.com/navigation`。

所有 API 请求使用 HTTPS。搜索和路线耗时取自高德；直线距离通过两点坐标计算并单独标注。导航链接不带 Key。若路线失败，仍输出已有真实 POI 并标明降级；若无 POI，返回空列表而不是编造数据。

## 检验

```bash
python -m unittest discover -s tests -p "test_amap_nearby.py" -v
python scripts/amap_nearby.py --help
```

离线测试覆盖：
- 一句话提取地点、类型、半径、出行方式；
- 地理编码、POI 真实字段、去重、非法坐标过滤、导航链接；
- 步行/骑行路线解析；
- 路线不可用时保留地点、无搜索结果不捏造；
- 缺失 Key、缺失起点、无效输入及 JSON CLI 输出。

模拟测试**不能**证明高德线上 Key、配额、城市 POI 数据、服务可用性。提交或演示前请使用自己 Key 真实测试一次。

## 安全与边界

- 没有提供准确起点时应向用户澄清；不通过 IP 或猜测读取当前位置。
- 坐标必须为经度在前的高德 GCJ-02；不自动将 WGS-84 当作高德坐标。
- API Key 仅由环境变量提供；不要写入 commit、测试快照或回复。
- 不对商家经营状态、安静程度、插座、停车条件或评分做未被高德返回的断言。
- POI 2.0 的额度取决于高德账号服务权限；海外地点可能需要额外权限。
- GitHub CI 无真实 Key，只能验证离线行为。最终比赛分数以官方提测为准。

## 来源

- [高德搜索 POI 2.0](https://developer.amap.com/api/webservice/guide/api/newpoisearch)
- [高德地理/逆地理编码](https://developer.amap.com/api/webservice/guide/api/georegeo/)
- [高德路径规划 2.0](https://developer.amap.com/api/webservice/guide/api/newroute)
- [高德 URI 路线导航](https://developer.amap.com/api/uri-api/guide/travel/route)
