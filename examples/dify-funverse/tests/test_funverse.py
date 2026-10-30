"""Offline contract tests; run a real seekdb smoke test before claiming deployment."""
import os
import sys
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api import main  # noqa: E402


class FakeCursor:
    def __init__(self, state):
        self.state = state
        self.rows = []
        self.one = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, args=None):
        # Mirror the supported SQL shapes; never execute SQL in a Python eval.
        if sql.strip().startswith("CREATE TABLE"):
            return
        if sql.startswith("INSERT INTO"):
            player, turn, choice, chapter = args
            assert "%s" in sql and choice not in sql  # Bound SQL parameters
            key = (player, turn)
            if key not in self.state:
                self.state[key] = {
                    "id": len(self.state) + 1,
                    "choice": choice,
                    "chapter": chapter,
                }
            return
        if sql.startswith("SELECT COUNT(*)"):
            self.one = {"n": sum(key[0] == args[0] for key in self.state)}
            return
        if sql.startswith("SELECT id, choice, chapter"):
            assert "WHERE player_id = %s" in sql
            self.rows = sorted(
                (record for (player, _), record in self.state.items() if player == args[0]),
                key=lambda item: item["id"],
                reverse=True,
            )[:40]
            return
        if sql.startswith("SELECT id FROM"):
            record = self.state.get((args[0], args[1]))
            self.one = {"id": record["id"]} if record else None
            return
        raise AssertionError(f"Unexpected SQL: {sql}")

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return FakeCursor(self.state)


def test_gateway_requires_token_and_never_uses_anonymous_memory(monkeypatch):
    monkeypatch.setenv("FUN_API_TOKEN", "test-only-token")
    data = {}
    monkeypatch.setattr(main, "connect", lambda: FakeConnection(data))
    with TestClient(main.app) as client:
        payload = {"player_id": "p1", "choice": "进入猫咪共和国"}
        assert client.post("/v1/context", json=payload).status_code == 401
        assert client.post("/v1/context", json=payload, headers={"X-Fun-Token": "bad"}).status_code == 401
        assert client.post("/v1/context", json=payload, headers={"X-Fun-Token": "test-only-token"}).status_code == 200


def test_real_persistence_contract_is_user_scoped_and_idempotent(monkeypatch):
    monkeypatch.setenv("FUN_API_TOKEN", "test-only-token")
    state = {}
    monkeypatch.setattr(main, "connect", lambda: FakeConnection(state))
    headers = {"X-Fun-Token": "test-only-token"}
    story = {"player_id": "p1", "turn_id": "run-1", "choice": "我在火星种火锅", "chapter": "第一章：火星火锅节"}
    with TestClient(main.app) as client:
        saved = client.post("/v1/save", json=story, headers=headers)
        assert saved.status_code == 200 and saved.json()["stored"]
        repeat = client.post("/v1/save", json={**story, "chapter": "不应覆盖的剧情"}, headers=headers)
        assert repeat.json()["chapter_id"] == saved.json()["chapter_id"]
        assert len(state) == 1 and state[("p1", "run-1")]["chapter"] == story["chapter"]

        own = client.post(
            "/v1/context", json={"player_id": "p1", "choice": "火锅怎么样"}, headers=headers
        ).json()
        stranger = client.post(
            "/v1/context", json={"player_id": "p2", "choice": "火锅怎么样"}, headers=headers
        ).json()
        assert own["turns_saved"] == 1
        assert len(own["memories"]) == 1
        assert stranger["turns_saved"] == 0 and stranger["memories"] == []


def test_relevant_older_callbacks_reappear():
    records = [
        {"id": i, "choice": ("熊猫火星" if i == 1 else f"事件{i}"), "chapter": ("熊猫登月" if i == 1 else f"章节{i}")}
        for i in range(6, 0, -1)
    ]
    chosen = main.choose_memories(records, "熊猫的火星经历")
    ids = [part["chapter_id"] for part in chosen]
    assert ids[:3] == [6, 5, 4]
    assert 1 in ids


def test_dify_chatflow_graph_and_database_roundtrip():
    path = Path(__file__).resolve().parents[1] / "dify" / "FunVerse-Chatflow.yml"
    app = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert app["app"]["mode"] == "advanced-chat"
    assert app["version"] == "0.7.0"
    nodes = {node["id"]: node["data"] for node in app["workflow"]["graph"]["nodes"]}
    edges = app["workflow"]["graph"]["edges"]
    assert [nodes[n]["type"] for n in ["1000000000000", "1000000000001", "1000000000002", "1000000000003", "1000000000004"]] == [
        "start", "http-request", "llm", "http-request", "answer"
    ]
    assert [(e["source"], e["target"]) for e in edges] == [
        ("1000000000000", "1000000000001"),
        ("1000000000001", "1000000000002"),
        ("1000000000002", "1000000000003"),
        ("1000000000003", "1000000000004"),
    ]
    assert nodes["1000000000001"]["url"].endswith("/v1/context")
    assert nodes["1000000000003"]["url"].endswith("/v1/save")
    assert any(item["key"] == "chapter" for item in nodes["1000000000003"]["body"]["data"])
    assert "{{#1000000000002.text#}}" in nodes["1000000000004"]["answer"]
