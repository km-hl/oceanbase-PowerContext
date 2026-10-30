---
name: amap-nearby
description: 基于高德地图 Web 服务 API 的周边探索助手。用户询问“附近有什么好玩的”“从某地出发找咖啡馆/书店/公园”“步行或骑行能到哪里”时使用；返回真实地点、可核实距离、路线和高德导航链接。
---

# 周边去哪儿（高德地图 Skill）

## 何时使用
当用户用自然语言提出周边地点推荐、到达方式、出行距离、路线等需求时使用。**必须使用实时高德 API 的真实 POI 和路线数据，不得生成虚构商家或虚构耗时。**

## 准备
- 在 [高德开放平台](https://console.amap.com/dev/key/app) 创建 **Web 服务** Key。
- 将 Key 放在运行环境的 `AMAP_API_KEY` 中。不能写入仓库、提交日志、URL 示例或最终回复。
- 此 Skill 使用 Python 3.11+ 标准库，无需安装额外依赖。
- 从仓库根目录执行 `python scripts/amap_nearby.py`。

## 交互流程（简短但完整）
1. 从用户的一句话识别想找的场所、起点、距离偏好和出行方式。支持咖啡馆、公园、书店、博物馆、餐饮等关键词，也可用 `--category` 直接指定。
2. **先确认起点**。若用户提供的是地标/地址，用 `--location` 并由高德地理编码；若用户明确提供 GCJ-02 经纬度，使用 `--coordinates 经度,纬度`。脚本亦可从「我在北京望京SOHO附近，想找咖啡厅」提取起点。未找到时只询问起点，**不猜当前位置，不静默基于 IP 定位**。
3. 使用高德 POI 2.0 周边搜索（`/v5/place/around`），按半径筛选、去重、根据直线距离排序，最多推荐 5 个（可用 `--limit` 修改，最多 10 个）。
4. 对每个推荐地点用高德路线规划 2.0 查询步行、骑行或驾车距离和耗时；若路线失败，仍可展示真实 POI 和导航链接，但**明确写“路线暂不可用”**。
5. 向用户输出名称、真实地址、直线距离、推荐理由、路线距离/时间（如有）、高德导航链接；评分及参考消费仅在高德返回时展示。不要据此推测营业中、安静程度、有无插座、无障碍设施或交通实时状态。

## 使用示例

在 Linux/macOS：
```bash
export AMAP_API_KEY="请填写自己的高德Web服务Key"
python scripts/amap_nearby.py \
  --query "我在北京望京SOHO附近，想找2公里内可以步行去的咖啡馆" \
  --limit 5 --format markdown
```

在 Windows PowerShell：
```powershell
$env:AMAP_API_KEY = "请填写自己的高德Web服务Key"
python scripts/amap_nearby.py --query "附近有什么好玩的公园" --location "北京市朝阳区望京SOHO" --format json
```

使用已知坐标（避免地理编码）：
```bash
python scripts/amap_nearby.py --query "想找附近书店，骑行前往" \
  --coordinates "116.481499,39.990475" --radius 3000 --limit 3
```

帮助：`python scripts/amap_nearby.py --help`

## 输出约定
- Markdown：面向用户，包含地点、依据与地图链接；`--format json`：面向 Agent 的结构化字段。
- JSON 的 `recommendations[].straight_distance_m` 为坐标计算的**直线距离**，非路线距离。
- `recommendations[].route.distance_m` 和 `duration_min` 来自高德路线接口；无值时为 `null`，不估算、不捏造。
- `warnings` 会说明路线查询失败、找不到地点或其他可恢复情况。无 POI 时结果列表为空。
- 受 Key 权限、服务配额、地图覆盖范围影响，部分请求可能失败。不存在真实 Key 的环境下不可宣称完成在线验证。

## 运行测试
```bash
python -m unittest discover -s tests -p "test_amap_nearby.py" -v
```

上述测试使用固定响应和依赖注入，只证明本地逻辑，不等于真实高德网络可用。建议配置自己的 Key 后做一次真实查询并核对地图。

## API 文档
- [高德 POI 2.0](https://developer.amap.com/api/webservice/guide/api/newpoisearch)
- [高德地理编码](https://developer.amap.com/api/webservice/guide/api/georegeo/)
- [高德路线规划 2.0](https://developer.amap.com/api/webservice/guide/api/newroute)
- [高德导航 URI](https://developer.amap.com/api/uri-api/guide/travel/route)
