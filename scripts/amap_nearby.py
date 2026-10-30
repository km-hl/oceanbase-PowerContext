#!/usr/bin/env python3
"""高德周边去哪儿 Skill：真实 POI 检索、路线规划与可解释推荐（Python 标准库）。"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

API = "https://restapi.amap.com"
CATEGORIES = (
    ("咖啡", "咖啡厅"), ("火锅", "火锅"), ("烧烤", "烧烤"), ("奶茶", "奶茶"),
    ("书店", "书店"), ("图书馆", "图书馆"), ("博物馆", "博物馆"),
    ("美术馆", "美术馆"), ("公园", "公园"), ("景点", "风景名胜"),
    ("电影院", "电影院"), ("电影", "电影院"), ("健身房", "健身"),
    ("超市", "超市"), ("医院", "医院"), ("药店", "药店"),
    ("餐馆", "餐饮"), ("餐厅", "餐饮"), ("吃饭", "餐饮"), ("美食", "餐饮"),
    ("散步", "公园"), ("遛娃", "公园"), ("约会", "咖啡厅"),
)
MODES = {"walk": "步行", "bike": "骑行", "drive": "驾车"}
NAV_MODES = {"walk": "walk", "bike": "ride", "drive": "car"}


class ExploreError(Exception):
    """可向用户展示的错误；不包含凭证或完整请求地址。"""


def coords(value: str) -> str:
    """规范化 GCJ-02 经度,纬度，防止输入非法坐标。"""
    parts = value.split(",")
    if len(parts) != 2:
        raise ExploreError("坐标格式应为 经度,纬度，例如 116.481499,39.990475（GCJ-02）。")
    try:
        lon, lat = map(float, parts)
    except ValueError as exc:
        raise ExploreError("坐标必须是经度,纬度两个数字。") from exc
    if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
        raise ExploreError("坐标超出合法范围。")
    return f"{lon:.6f},{lat:.6f}"


def straight_distance(a: str, b: str) -> int:
    lon1, lat1 = map(math.radians, map(float, coords(a).split(",")))
    lon2, lat2 = map(math.radians, map(float, coords(b).split(",")))
    x = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return round(12742000 * math.asin(min(1, math.sqrt(x))))


def infer_query(query: str, category: str = "", radius: int = 3000, mode: str = "") -> dict:
    """将简单自然语言需求转换成可审计的高德参数，不调用模型或猜测个人位置。"""
    if not query.strip():
        raise ExploreError("请输入希望探索的地点需求。")
    chosen = category.strip()
    if not chosen:
        for phrase, keyword in CATEGORIES:
            if phrase in query:
                chosen = keyword
                break
    if not chosen:
        raise ExploreError("未识别地点类型，请提供 --category（例如 咖啡厅、公园、博物馆）。")
    if len(chosen) > 80:
        raise ExploreError("搜索关键词不能超过80个字符。")
    found = re.search(r"(\d+(?:\.\d+)?)\s*(公里|km|千米|米|m)\s*(?:内|以内|范围|附近)?", query, re.I)
    if found:
        distance = float(found.group(1))
        radius = round(distance * (1000 if found.group(2).lower() in ("公里", "km", "千米") else 1))
    if not 1 <= radius <= 50000:
        raise ExploreError("周边搜索半径需在1至50000米之间。")
    if not mode:
        mode = "bike" if "骑行" in query or "骑车" in query else (
            "drive" if any(x in query for x in ("驾车", "开车", "自驾")) else "walk"
        )
    if mode not in MODES:
        raise ExploreError("出行模式只支持 walk、bike、drive。")
    return {"category": chosen, "radius": radius, "mode": mode}


def infer_origin(query: str) -> str:
    found = re.search(r"(?:我在|位于|从|在)([^，。！？,]{2,40}?)(?:附近|周围|周边|出发|想找|找个|找一家|想去)", query)
    return found.group(1).strip() if found else ""


class AmapClient:
    def __init__(self, key: str, fetch=None):
        if not key or not key.strip():
            raise ExploreError("未配置 AMAP_API_KEY，请先申请高德 Web 服务 Key 并设置环境变量。")
        self.key = key.strip()
        self.fetch = fetch or self._http_fetch

    @staticmethod
    def _http_fetch(path: str, params: dict) -> dict:
        url = API + path + "?" + urlencode(params)
        try:
            with urlopen(url, timeout=8) as response:
                result = json.load(response)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            # 不将含 API Key 的 URL / 异常详情暴露给调用者。
            raise ExploreError("高德请求失败（网络、配额或响应格式异常），请稍后重试。") from exc
        if not isinstance(result, dict):
            raise ExploreError("高德返回的数据格式无效。")
        return result

    def call(self, path: str, params: dict) -> dict:
        data = self.fetch(path, {"key": self.key, **params})
        if not isinstance(data, dict) or str(data.get("status")) != "1":
            info = str(data.get("info", "UNKNOWN")) if isinstance(data, dict) else "INVALID_RESPONSE"
            infocode = str(data.get("infocode", "")) if isinstance(data, dict) else ""
            # 将第三方错误字段截短，避免异常打印恶意/意外长内容。
            raise ExploreError(f"高德 API 返回失败：{info[:80]}（{infocode[:16]}）。")
        return data

    def geocode(self, address: str, city: str = "") -> str:
        if not address.strip():
            raise ExploreError("缺少起点。请用 --location 提供位置名称，或用 --coordinates 提供 GCJ-02 坐标。")
        params = {"address": address.strip()}
        if city:
            params["city"] = city
        data = self.call("/v3/geocode/geo", params)
        entries = data.get("geocodes", [])
        if not isinstance(entries, list) or not entries:
            raise ExploreError("未查到起点地址，请使用更完整的地址或 --coordinates。")
        return coords(str(entries[0]["location"]))

    def around(self, origin: str, keyword: str, radius: int, size: int = 20) -> list:
        data = self.call("/v5/place/around", {
            "location": origin, "keywords": keyword, "radius": radius,
            "sortrule": "distance", "show_fields": "business", "page_size": min(size, 25),
            "page_num": 1,
        })
        items = data.get("pois", [])
        return items if isinstance(items, list) else []

    def route(self, origin: str, destination: str, mode: str) -> dict:
        data = self.call("/v5/direction/" + {"walk": "walking", "bike": "bicycling", "drive": "driving"}[mode],
                         {"origin": origin, "destination": destination, "show_fields": "cost"})
        options = data.get("route", {}).get("paths", [])
        if not isinstance(options, list) or not options:
            raise ExploreError("高德未返回可用的路线。")
        option = options[0]
        cost = option.get("cost") or {}
        duration = cost.get("duration") or option.get("duration")
        try:
            seconds = round(float(duration)) if duration not in (None, "") else None
            meters = round(float(option["distance"]))
        except (ValueError, TypeError, KeyError) as exc:
            raise ExploreError("路线的距离或耗时数据缺失。") from exc
        return {"distance_m": meters, "duration_min": math.ceil(seconds / 60) if seconds is not None else None}


def nav_link(start: str, dest: str, name: str, mode: str) -> str:
    """官方高德 URI 导航入口，坐标为 GCJ-02；链接不包含 API Key。"""
    return "https://uri.amap.com/navigation?" + urlencode({
        "from": start + ",起点", "to": dest + "," + name,
        "mode": NAV_MODES[mode], "src": "oceanbase.nearby-skill", "coordinate": "gaode",
    })


def explore(query: str, client: AmapClient, *, location: str = "", coordinates: str = "",
            city: str = "", category: str = "", radius: int = 3000,
            mode: str = "", limit: int = 5) -> dict:
    intent = infer_query(query, category=category, radius=radius, mode=mode)
    if not 1 <= limit <= 10:
        raise ExploreError("推荐数量需在1至10之间。")
    source = coordinates or location or infer_origin(query)
    if not source:
        raise ExploreError("无法确定你的起点，请提供 --location 或 --coordinates；不会擅自读取定位。")
    origin = coords(source) if coordinates or re.fullmatch(r"\s*-?\d+(?:\.\d+)?,\s*-?\d+(?:\.\d+)?\s*", source) else client.geocode(source, city)
    raw = client.around(origin, intent["category"], intent["radius"], size=max(15, limit * 3))
    seen = set()
    candidates = []
    for poi in raw:
        if not isinstance(poi, dict):
            continue
        try:
            dest = coords(str(poi["location"]))
            name = str(poi["name"]).strip()
            meters = straight_distance(origin, dest)
        except (ExploreError, ValueError, TypeError, KeyError):
            continue
        if not name or meters > intent["radius"]:
            continue
        identity = str(poi.get("id") or (name, dest))
        if identity in seen:
            continue
        seen.add(identity)
        business = poi.get("business") if isinstance(poi.get("business"), dict) else {}
        result = {
            "id": str(poi.get("id") or ""), "name": name,
            "address": str(poi.get("address") or ""),
            "coordinates": dest, "straight_distance_m": meters,
            "category": str(poi.get("type") or ""),
            "rating": str(business.get("rating") or "") or None,
            "cost": str(business.get("cost") or "") or None,
            "tag": str(business.get("tag") or "") or None,
        }
        candidates.append(result)
    candidates.sort(key=lambda x: (x["straight_distance_m"], x["name"]))
    choices = candidates[:limit]
    warnings = []
    for item in choices:
        item["navigation_url"] = nav_link(origin, item["coordinates"], item["name"], intent["mode"])
        try:
            item["route"] = client.route(origin, item["coordinates"], intent["mode"])
        except ExploreError:
            item["route"] = None
            warnings.append(f"{item['name']} 的路线暂不可用；仍可点击导航链接查看。")
        reason = f"与“{intent['category']}”搜索条件匹配，直线距离约 {item['straight_distance_m']} 米"
        if item["route"] and item["route"].get("duration_min") is not None:
            reason += f"，{MODES[intent['mode']]}预计约 {item['route']['duration_min']} 分钟"
        item["reason"] = reason + "。"
    if not choices:
        warnings.append("此范围内没有匹配地点，可换关键词或扩大搜索半径。")
    return {
        "query": query, "origin": origin, "search_radius_m": intent["radius"],
        "keyword": intent["category"], "travel_mode": intent["mode"],
        "recommendations": choices, "warnings": warnings,
        "data_source": "高德地图 Web 服务 API（POI 2.0 / 路线规划 2.0）",
        "note": "直线距离不是实际行走距离；营业、环境安静程度和设施需向商家核实。",
    }


def markdown(data: dict) -> str:
    lines = [f"# 周边去哪儿：{data['keyword']}", "",
             f"起点坐标：{data['origin']}；范围：{data['search_radius_m']} 米；出行：{MODES[data['travel_mode']]}。",
             ""]
    for n, poi in enumerate(data["recommendations"], 1):
        lines += [f"## {n}. {poi['name']}", f"- 地址：{poi['address'] or '未提供'}",
                  f"- 推荐理由：{poi['reason']}"]
        if poi["route"]:
            route = poi["route"]
            duration = f"{route['duration_min']} 分钟" if route["duration_min"] is not None else "未知"
            lines.append(f"- {MODES[data['travel_mode']]}路线：{route['distance_m']} 米，预计 {duration}")
        else:
            lines.append("- 路线：暂不可用（未猜测耗时）")
        if poi["rating"]:
            lines.append(f"- 高德评分：{poi['rating']}")
        if poi["cost"]:
            lines.append(f"- 高德人均参考消费：{poi['cost']}")
        lines += [f"- [打开高德地图导航]({poi['navigation_url']})", ""]
    lines += [*(f"> 提示：{warning}" for warning in data["warnings"]), "", data["note"]]
    return "\n".join(lines).strip()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="一句话探索附近地点，并查询高德真实路线")
    parser.add_argument("--query", required=True, help="自然语言需求，例如：想在望京附近找个咖啡馆")
    parser.add_argument("--location", default="", help="起点地址或地标（优先于从需求中推测）")
    parser.add_argument("--coordinates", default="", help="起点 GCJ-02 经度,纬度（优先级最高）")
    parser.add_argument("--city", default="", help="地址地理编码的城市限定，可选")
    parser.add_argument("--category", default="", help="明确搜索关键词，可选")
    parser.add_argument("--radius", type=int, default=3000, help="搜索半径（米），默认3000")
    parser.add_argument("--mode", choices=tuple(MODES), default="", help="walk/bike/drive，默认从需求推断")
    parser.add_argument("--limit", type=int, default=5, help="推荐数量1-10")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args(argv)
    try:
        result = explore(args.query, AmapClient(os.environ.get("AMAP_API_KEY", "")),
                         location=args.location, coordinates=args.coordinates, city=args.city,
                         category=args.category, radius=args.radius, mode=args.mode, limit=args.limit)
    except ExploreError as exc:
        if args.format == "json":
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"查询失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
