
import pytest
from fastapi.testclient import TestClient

from demo.chat import server

PASS = "open sesame"


@pytest.fixture
def client(tmp_path):
    (tmp_path / "owner/memories").mkdir(parents=True)
    (tmp_path / "owner/memories/MEMORY.md").write_text("a\n§\nb", encoding="utf-8")
    s = server.Settings(
        {
            "DOTV_OWNER_HOME": str(tmp_path / "owner"),
            "DOTV_PUBLIC_HOME": str(tmp_path / "public"),
            "DOTV_OWNER_PASSPHRASE_HASH": server.hash_passphrase(PASS),
            "DOTV_SECRET": "x" * 32,
            "DOTV_OWNER_KEY": "ok",
            "DOTV_PUBLIC_KEY": "pk",
        }
    )
    return TestClient(server.create_app(s))


def test_passphrase_hash_roundtrip():
    h = server.hash_passphrase("p")
    assert server.verify_passphrase("p", h) and not server.verify_passphrase("q", h)
    assert h.split("$")[0] == "scrypt" and len(h.split("$")) == 3


def test_guest_by_default_and_board_locked(client):
    assert client.get("/api/me").json()["role"] == "guest"
    assert client.get("/api/board/items").status_code == 403
    assert client.post("/api/board/save", json={"public_ids": []}).status_code == 403


def test_unlock_lock_and_rate_limit(client):
    for _ in range(4):
        assert client.post("/api/unlock", json={"passphrase": "nope"}).status_code == 401
    assert client.post("/api/unlock", json={"passphrase": PASS}).json()["role"] == "owner"
    assert len(client.get("/api/board/items").json()["items"]) == 2
    assert client.post("/api/lock", json={}).json()["role"] == "guest"
    for _ in range(5):
        client.post("/api/unlock", json={"passphrase": "nope"})
    assert client.post("/api/unlock", json={"passphrase": PASS}).status_code == 429


def test_board_list_is_light_and_bodies_load_on_demand(client):
    for path in ("/api/board/text?id=x", "/api/board/search?q=a"):
        assert client.get(path).status_code == 403
    client.post("/api/unlock", json={"passphrase": PASS})
    items = client.get("/api/board/items").json()["items"]
    assert items and all("text" not in i for i in items)
    texts = {i["id"]: client.get("/api/board/text", params={"id": i["id"]}).json()["text"] for i in items}
    assert sorted(texts.values()) == ["a", "b"]
    hit = client.get("/api/board/search", params={"q": "B"}).json()["ids"]
    assert [texts[i] for i in hit] == ["b"]
    assert client.get("/api/board/text", params={"id": "gone"}).status_code == 404


def test_forged_cookie_is_guest(client):
    client.cookies.set("dotv", "eyJvd25lcl9leHAiOjk5OTk5OTk5OTl9.deadbeef")
    assert client.get("/api/me").json()["role"] == "guest"


def test_json_required(client):
    assert (
        client.post(
            "/api/unlock", data="passphrase=x", headers={"content-type": "application/x-www-form-urlencoded"}
        ).status_code
        == 415
    )


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
    from salts_secrets import tiers as perm

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
    assert client.get("/api/me").json()["role"] == "blocked"  # deleting a member does not lift the block


def test_perm_board_owner_only(client, tmp_path):
    assert client.get("/perm", follow_redirects=False).status_code in (302, 307)
    client.post("/api/unlock", json={"passphrase": PASS})
    st = client.get("/api/perm").json()
    assert st["policy"]["guest"] and any(r["locked"] for r in st["catalog"])
    j = client.post("/api/perm/members", json={"action": "add", "name": "a", "tier": "admin"}).json()
    assert j["code"].startswith("inv-") and j["members"][0]["tier"] == "admin" and "code_hash" not in j["members"][0]
    assert client.post("/api/perm/members", json={"action": "add", "name": "a", "tier": "owner"}).status_code == 400


def _owner_client(client):
    owner = TestClient(client.app)
    assert owner.post("/api/unlock", json={"passphrase": PASS}).json()["role"] == "owner"
    return owner


def test_blocked_member_cannot_come_back_by_clearing_cookie(client):
    owner = _owner_client(client)
    client = TestClient(client.app, client=("203.0.113.7", 50000))
    code = owner.post("/api/perm/members", json={"action": "add", "name": "x", "tier": "user"}).json()["code"]
    assert client.post("/api/unlock", json={"passphrase": code}).json()["role"] == "user"
    mid = owner.get("/api/perm").json()["members"][0]["id"]
    owner.post("/api/perm/members", json={"action": "update", "id": mid, "tier": "blocked"})
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 403
    client.cookies.clear()
    assert client.get("/api/me").json()["role"] == "blocked"
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 403
    assert client.post("/api/unlock", json={"passphrase": PASS}).json()["role"] == "owner"
    blocks = owner.get("/api/perm").json()["blocklist"]
    assert {b["kind"] for b in blocks} == {"ip", "device"}
    for b in blocks:
        owner.post("/api/perm/access", json={"action": "unblock", "kind": b["kind"], "value": b["value"]})
    assert TestClient(client.app, client=("203.0.113.7", 50001)).get("/api/me").json()["role"] == "guest"


def test_proxy_address_is_never_ip_blocked(client):
    owner = _owner_client(client)
    code = owner.post("/api/perm/members", json={"action": "add", "name": "x", "tier": "user"}).json()["code"]
    proxied = TestClient(client.app, client=("172.18.0.1", 50000))
    proxied.post("/api/unlock", json={"passphrase": code})
    mid = owner.get("/api/perm").json()["members"][0]["id"]
    owner.post("/api/perm/members", json={"action": "update", "id": mid, "tier": "blocked"})
    assert [b["kind"] for b in owner.get("/api/perm").json()["blocklist"]] == ["device"]
    assert proxied.get("/api/me").json()["role"] == "blocked"
    assert TestClient(client.app, client=("172.18.0.1", 50001)).get("/api/me").json()["role"] == "guest"


def test_guest_access_switch(client):
    owner = _owner_client(client)
    owner.post("/api/perm/access", json={"action": "guest_access", "enabled": False})
    assert client.get("/api/me").json()["guest_access"] is False
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 403
    assert client.post("/api/perm/access", json={"action": "guest_access", "enabled": True}).status_code == 403


def test_only_owner_can_switch_models(client, tmp_path, monkeypatch):
    (tmp_path / "owner/config.yaml").write_text(
        "model:\n  provider: ds\n  default: deepseek-flash\nproviders:\n"
        "  bai:\n    api_key: sk-secret\n    model: claude-opus-5.5\n  ds:\n    model: deepseek-flash\n",
        encoding="utf-8",
    )
    sent = []

    class FakeStream:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aiter_lines(self):
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            sent.append(json)
            return FakeStream()

    monkeypatch.setattr(server.httpx, "AsyncClient", FakeClient)
    assert "models" not in client.get("/api/me").json()
    assert client.post("/api/chat", json={"message": "hi", "model": "bai"}).status_code == 403
    me = client.post("/api/unlock", json={"passphrase": PASS}).json()
    assert me["models"] == [{"id": "ds", "label": "deepseek-flash"}, {"id": "bai", "label": "claude-opus-5.5"}]
    assert "sk-secret" not in str(me)
    client.post("/api/chat", json={"message": "hi"})
    client.post("/api/chat", json={"message": "hi", "model": "ds"})
    client.post("/api/chat", json={"message": "hi", "model": "bai"})
    assert client.post("/api/chat", json={"message": "hi", "model": "nope"}).status_code == 400
    plain, default, opus = sent
    assert plain == default and "provider" not in plain and plain["model"] == "hermes-agent"
    assert opus["provider"] == "bai" and opus["model"] == "claude-opus-5.5"


def test_pages_use_configured_names(tmp_path):
    s = server.Settings({"DOTV_OWNER_HOME": str(tmp_path), "DOTV_OWNER_NAME": "Ada", "DOTV_PUBLIC_NAME": "Byte"})
    html = TestClient(server.create_app(s)).get("/").text
    assert "Byte's assistant" in html and "Hi Ada." in html
    assert "Salt" not in html and "Lena" not in html
