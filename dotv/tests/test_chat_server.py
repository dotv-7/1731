import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotv.chat import server

PASS = "open sesame"


@pytest.fixture
def client(tmp_path):
    (tmp_path / "owner/memories").mkdir(parents=True)
    (tmp_path / "owner/memories/MEMORY.md").write_text("a\n§\nb", encoding="utf-8")
    s = server.Settings({"DOTV_OWNER_HOME": str(tmp_path / "owner"), "DOTV_PUBLIC_HOME": str(tmp_path / "public"),
                         "DOTV_OWNER_PASSPHRASE_HASH": server.hash_passphrase(PASS), "DOTV_SECRET": "x" * 32,
                         "DOTV_OWNER_KEY": "ok", "DOTV_PUBLIC_KEY": "pk"})
    return TestClient(server.create_app(s))


def test_passphrase_hash_roundtrip():
    h = server.hash_passphrase("p")
    assert server.verify_passphrase("p", h) and not server.verify_passphrase("q", h)
    assert h.split("$")[0] == "scrypt" and len(h.split("$")) == 3


def test_guest_by_default_and_board_locked(client):
    assert client.get("/api/me").json()["role"] == "guest"
    assert client.get("/api/board/items").status_code == 403
    assert client.post("/api/board/save", json={"public_ids": []}).status_code == 403
    assert client.get("/board", follow_redirects=False).status_code in (302, 307)


def test_unlock_lock_and_rate_limit(client):
    for _ in range(4):
        assert client.post("/api/unlock", json={"passphrase": "nope"}).status_code == 401
    assert client.post("/api/unlock", json={"passphrase": PASS}).json()["role"] == "owner"
    assert len(client.get("/api/board/items").json()["items"]) == 2
    assert client.post("/api/lock", json={}).json()["role"] == "guest"
    for _ in range(5):
        client.post("/api/unlock", json={"passphrase": "nope"})
    assert client.post("/api/unlock", json={"passphrase": PASS}).status_code == 429


def test_forged_cookie_is_guest(client):
    client.cookies.set("dotv", "eyJvd25lcl9leHAiOjk5OTk5OTk5OTl9.deadbeef")
    assert client.get("/api/me").json()["role"] == "guest"


def test_json_required(client):
    assert client.post("/api/unlock", data="passphrase=x",
                       headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 415


def test_chat_routes_by_role(client, monkeypatch):
    seen = []

    class FakeStream:
        def __init__(self, url, headers):
            seen.append((url, headers))
            self.status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"hi"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            return FakeStream(url, headers)

    monkeypatch.setattr(server.httpx, "AsyncClient", FakeClient)
    r = client.post("/api/chat", json={"message": "hello"})
    assert '"t": "hi"' in r.text
    client.post("/api/unlock", json={"passphrase": PASS})
    client.post("/api/chat", json={"message": "hello"})
    (g_url, g_h), (o_url, o_h) = seen
    assert g_url.endswith("/p/public/v1/chat/completions") and g_h["Authorization"] == "Bearer pk"
    assert o_url.endswith("/v1/chat/completions") and "/p/" not in o_url and o_h["Authorization"] == "Bearer ok"
    assert g_h["X-Hermes-Session-Id"].startswith("dotv-g-") and o_h["X-Hermes-Session-Id"].startswith("dotv-o-")


def test_member_codes_route_to_tier_and_blocked_cannot_chat(client, tmp_path):
    from dotv.perm import core as perm
    owner = tmp_path / "owner"
    m, code = perm.add_member(owner, "小王", "user")
    r = client.post("/api/unlock", json={"passphrase": code})
    assert r.json()["role"] == "user" and r.json()["name"] == "小王"
    assert client.get("/api/board/items").status_code == 403 and client.get("/api/perm").status_code == 403
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 503  # user tier key not configured
    perm.update_member(owner, m["id"], tier="blocked")
    assert client.get("/api/me").json()["role"] == "blocked"
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 403
    assert client.post("/api/lock", json={}).status_code == 403
    perm.update_member(owner, m["id"], delete=True)
    assert client.get("/api/me").json()["role"] == "guest"


def test_perm_board_and_doc_owner_only(client, tmp_path):
    assert client.get("/api/doc").status_code == 403
    assert client.get("/perm", follow_redirects=False).status_code in (302, 307)
    client.post("/api/unlock", json={"passphrase": PASS})
    st = client.get("/api/perm").json()
    assert st["policy"]["guest"] and any(r["locked"] for r in st["catalog"])
    j = client.post("/api/perm/members", json={"action": "add", "name": "a", "tier": "admin"}).json()
    assert j["code"].startswith("inv-") and j["members"][0]["tier"] == "admin" and "code_hash" not in j["members"][0]
    assert client.post("/api/perm/members", json={"action": "add", "name": "a", "tier": "owner"}).status_code == 400
