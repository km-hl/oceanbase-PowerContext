"""周边去哪儿 Skill 的离线行为测试：只模拟高德 API 响应，不耗费真实配额。"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / "scripts" / "amap_nearby.py"
spec = importlib.util.spec_from_file_location("amap_nearby_skill", FILE)
amap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(amap)

START = "116.481499,39.990475"


def fixture(path, params):
    if path == "/v3/geocode/geo":
        return {"status": "1", "geocodes": [{"location": START}]}
    if path == "/v5/place/around":
        return {"status": "1", "pois": [
            {"id": "a", "name": "A咖啡馆", "location": "116.482499,39.990475",
             "type": "餐饮服务", "address": "北京市朝阳区甲路1号",
             "business": {"rating": "4.6", "cost": "35"}},
            {"id": "a", "name": "重复A咖啡馆", "location": "116.482499,39.990475"},
            {"id": "b", "name": "B咖啡馆", "location": "116.490499,39.990475"},
            {"id": "far", "name": "远处", "location": "117.900000,39.990475"},
            {"id": "invalid", "name": "错误坐标", "location": "N/A"},
        ]}
    if path == "/v5/direction/walking":
        return {"status": "1", "route": {"paths": [{"distance": "290", "cost": {"duration": "660"}}]}}
    if path == "/v5/direction/bicycling":
        return {"status": "1", "route": {"paths": [{"distance": "350", "cost": {"duration": "180"}}]}}
    if path == "/v5/direction/driving":
        return {"status": "1", "route": {"paths": [{"distance": "600", "cost": {"duration": "230"}}]}}
    raise AssertionError(f"unexpected API: {path}")


class AmapNearbyTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fake(path, params):
            self.calls.append((path, params))
            return fixture(path, params)

        self.fake = fake
        self.client = amap.AmapClient("test-key", fetch=fake)

    def test_single_sentence_geocode_search_and_real_route(self):
        result = amap.explore("我在北京望京SOHO附近，想找2公里内咖啡馆，步行前往", self.client)
        self.assertEqual(result["keyword"], "咖啡厅")
        self.assertEqual(result["search_radius_m"], 2000)
        self.assertEqual(result["origin"], START)
        self.assertEqual([p["id"] for p in result["recommendations"]], ["a", "b"])
        first = result["recommendations"][0]
        self.assertEqual(first["route"]["duration_min"], 11)
        self.assertEqual(first["route"]["distance_m"], 290)
        self.assertEqual(first["rating"], "4.6")
        self.assertIn("直线距离", first["reason"])
        self.assertIn("https://uri.amap.com/navigation?", first["navigation_url"])
        self.assertNotIn("test-key", json.dumps(result, ensure_ascii=False))
        self.assertEqual(self.calls[0][0], "/v3/geocode/geo")
        self.assertEqual(self.calls[1][1]["page_size"], 15)

    def test_coordinates_bypass_geocode_and_bicycle_routing(self):
        result = amap.explore("找书店，骑车去", self.client, coordinates=START, limit=1)
        self.assertEqual(result["travel_mode"], "bike")
        self.assertEqual(result["recommendations"][0]["route"]["duration_min"], 3)
        self.assertTrue(all(path != "/v3/geocode/geo" for path, _ in self.calls))

    def test_partial_route_failure_is_explicit(self):
        def fail_route(path, params):
            if path.startswith("/v5/direction/"):
                return {"status": "0", "info": "SERVICE_NOT_AVAILABLE", "infocode": "30001"}
            return fixture(path, params)

        client = amap.AmapClient("test-key", fetch=fail_route)
        data = amap.explore("附近咖啡厅", client, coordinates=START, limit=1)
        self.assertIsNone(data["recommendations"][0]["route"])
        self.assertTrue(data["warnings"])
        self.assertIn("暂不可用", amap.markdown(data))
        self.assertIn("navigation_url", data["recommendations"][0])

    def test_empty_results_no_fabrication(self):
        client = amap.AmapClient("test-key", fetch=lambda path, params: {"status": "1", "pois": []})
        result = amap.explore("附近公园", client, coordinates=START)
        self.assertEqual(result["recommendations"], [])
        self.assertTrue(result["warnings"])

    def test_missing_origin_requires_user_input(self):
        with self.assertRaisesRegex(amap.ExploreError, "起点"):
            amap.explore("附近想找咖啡", self.client)

    def test_missing_key_is_clear_and_secret_is_not_logged(self):
        with self.assertRaisesRegex(amap.ExploreError, "AMAP_API_KEY"):
            amap.AmapClient("")
        def bad(path, params):
            return {"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}
        with self.assertRaises(amap.ExploreError) as err:
            amap.explore("附近咖啡厅", amap.AmapClient("secret123", fetch=bad), coordinates=START)
        self.assertNotIn("secret123", str(err.exception))

    def test_invalid_coords_radius_and_category(self):
        for value in ("x,y", "200,39", "116.0", "nan,20"):
            with self.subTest(value=value), self.assertRaises(amap.ExploreError):
                amap.coords(value)
        with self.assertRaises(amap.ExploreError):
            amap.infer_query("100公里内找书店")
        with self.assertRaises(amap.ExploreError):
            amap.infer_query("我想随便逛逛")

    def test_cli_json_output(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"AMAP_API_KEY": "test-key"}):
            with patch.object(amap.AmapClient, "_http_fetch", staticmethod(fixture)):
                with contextlib.redirect_stdout(output):
                    exit_code = amap.main(["--query", "附近咖啡馆", "--coordinates", START,
                                           "--limit", "1", "--format", "json"])
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(len(payload["recommendations"]), 1)
        self.assertEqual(payload["recommendations"][0]["route"]["distance_m"], 290)


if __name__ == "__main__":
    unittest.main()
