"""dotv-chat: independent terminal-style chat window + owner-only context dashboard.

The browser never sees a Hermes API key. Guests are proxied to the public profile
(``/p/<public>/v1``); an owner who unlocks with the passphrase is proxied to the default profile.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotv.context import core as ctx  # noqa: E402

STATIC = Path(__file__).parent / "static"
COOKIE = "dotv"
OWNER_TTL = 12 * 3600


class Settings:
    def __init__(self, env: Optional[dict] = None):
        e = os.environ if env is None else env
        self.hermes_url = e.get("DOTV_HERMES_URL", "http://127.0.0.1:8642").rstrip("/")
        self.owner_key = e.get("DOTV_OWNER_KEY", "")
        self.public_key = e.get("DOTV_PUBLIC_KEY", "")
        self.public_profile = e.get("DOTV_PUBLIC_PROFILE", "public")
        self.passphrase_hash = e.get("DOTV_OWNER_PASSPHRASE_HASH", "")
        self.secret = (e.get("DOTV_SECRET") or secrets.token_hex(32)).encode()
        self.owner_home = Path(e.get("DOTV_OWNER_HOME", "/opt/data"))
        self.public_home = Path(e.get("DOTV_PUBLIC_HOME", str(self.owner_home / "profiles" / self.public_profile)))
        roots = e.get("DOTV_WORKSPACE_ROOTS", "")
        self.workspace_roots = [Path(p) for p in roots.split(os.pathsep) if p]
        self.cookie_secure = e.get("DOTV_COOKIE_SECURE", "auto")


def hash_passphrase(passphrase: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(passphrase.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_passphrase(passphrase: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(passphrase.encode(), salt=bytes.fromhex(salt_hex), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    s = settings or Settings()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    attempts: dict[str, deque] = defaultdict(deque)

    def sign(data: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
        return raw + "." + hmac.new(s.secret, raw.encode(), hashlib.sha256).hexdigest()

    def read_state(request: Request) -> dict:
        raw, _, sig = (request.cookies.get(COOKIE) or "").partition(".")
        if raw and hmac.compare_digest(sig, hmac.new(s.secret, raw.encode(), hashlib.sha256).hexdigest()):
            try:
                st = json.loads(base64.urlsafe_b64decode(raw))
                if st.get("owner_exp", 0) < time.time():
                    st["owner_exp"] = 0
                return st
            except ValueError:
                pass
        return {"guest_sid": "", "owner_sid": "", "owner_exp": 0}

    def is_owner(st: dict) -> bool:
        return st.get("owner_exp", 0) > time.time()

    def respond(request: Request, st: dict, body: dict, status: int = 200) -> JSONResponse:
        resp = JSONResponse(body, status_code=status)
        secure = (s.cookie_secure == "true" or (s.cookie_secure == "auto" and (
            request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")))
        resp.set_cookie(COOKIE, sign(st), httponly=True, samesite="strict", secure=secure, max_age=180 * 86400)
        return resp

    def require_json(request: Request) -> None:
        if "application/json" not in request.headers.get("content-type", ""):
            raise HTTPException(415, "application/json required")

    def require_owner(request: Request) -> dict:
        st = read_state(request)
        if not is_owner(st):
            raise HTTPException(403, "owner only")
        return st

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/board")
    def board(request: Request):
        if not is_owner(read_state(request)):
            return RedirectResponse("/")
        return FileResponse(STATIC / "board.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request):
        st = read_state(request)
        return respond(request, st, {"role": "owner" if is_owner(st) else "guest",
                                     "owner_until": st.get("owner_exp", 0)})

    @app.post("/api/unlock")
    async def unlock(request: Request):
        require_json(request)
        ip = request.client.host if request.client else "?"
        window = attempts[ip]
        now = time.time()
        while window and window[0] < now - 300:
            window.popleft()
        if len(window) >= 5:
            raise HTTPException(429, "too many attempts, wait 5 minutes")
        window.append(now)
        body = await request.json()
        if not s.passphrase_hash or not verify_passphrase(str(body.get("passphrase", "")), s.passphrase_hash):
            raise HTTPException(401, "wrong passphrase")
        window.clear()
        st = read_state(request)
        st["owner_exp"] = int(now + OWNER_TTL)
        return respond(request, st, {"role": "owner", "owner_until": st["owner_exp"]})

    @app.post("/api/lock")
    def lock(request: Request):
        require_json(request)
        st = read_state(request)
        st["owner_exp"] = 0
        return respond(request, st, {"role": "guest"})

    @app.post("/api/new")
    def new_session(request: Request):
        require_json(request)
        st = read_state(request)
        st["owner_sid" if is_owner(st) else "guest_sid"] = ""
        return respond(request, st, {"ok": True})

    @app.post("/api/chat")
    async def chat(request: Request):
        require_json(request)
        body = await request.json()
        message = str(body.get("message", "")).strip()
        if not message or len(message) > 20000:
            raise HTTPException(400, "message must be 1-20000 chars")
        st = read_state(request)
        owner = is_owner(st)
        sid_key = "owner_sid" if owner else "guest_sid"
        if not st.get(sid_key):
            st[sid_key] = ("dotv-o-" if owner else "dotv-g-") + secrets.token_hex(16)
        key = s.owner_key if owner else s.public_key
        if not key:
            raise HTTPException(503, "Hermes key for this role is not configured")
        url = f"{s.hermes_url}/v1/chat/completions" if owner else \
            f"{s.hermes_url}/p/{s.public_profile}/v1/chat/completions"
        payload = {"model": "hermes-agent", "stream": True, "messages": [{"role": "user", "content": message}]}
        headers = {"Authorization": f"Bearer {key}", "X-Hermes-Session-Id": st[sid_key]}

        async def stream():
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
                    async with client.stream("POST", url, json=payload, headers=headers) as r:
                        if r.status_code != 200:
                            detail = (await r.aread()).decode(errors="replace")[:300]
                            yield _sse({"error": f"Hermes {r.status_code}: {detail}"})
                            return
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except ValueError:
                                continue
                            for choice in chunk.get("choices") or []:
                                text = (choice.get("delta") or {}).get("content")
                                if text:
                                    yield _sse({"t": text})
                yield _sse({"done": True})
            except httpx.HTTPError as exc:
                yield _sse({"error": f"cannot reach Hermes: {type(exc).__name__}"})

        resp = StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        secure = s.cookie_secure == "true" or (s.cookie_secure == "auto" and request.url.scheme == "https")
        resp.set_cookie(COOKIE, sign(st), httponly=True, samesite="strict", secure=secure, max_age=180 * 86400)
        return resp

    # ------------------------------------------------------------ owner-only context dashboard
    @app.get("/api/board/items")
    def board_items(request: Request):
        require_owner(request)
        items = ctx.scan(s.owner_home, s.workspace_roots)
        allow = ctx.load_allowlist(s.owner_home)
        return {"items": [dict(it.to_dict(), status=ctx.status(it, allow)) for it in items],
                "owner_home": str(s.owner_home.resolve()), "public_home": str(s.public_home.resolve()),
                "workspace_roots": [str(Path(r).resolve()) for r in s.workspace_roots]}

    @app.post("/api/board/save")
    async def board_save(request: Request):
        require_json(request)
        require_owner(request)
        ids = (await request.json()).get("public_ids") or []
        items = ctx.scan(s.owner_home, s.workspace_roots)
        ctx.save_allowlist(s.owner_home, items, [str(i) for i in ids])
        projection = ctx.sync_public(s.owner_home, s.public_home, s.workspace_roots)
        return {"facts": len(projection["facts"]), "documents": len(projection["documents"])}

    @app.get("/api/board/preview")
    def board_preview(request: Request):
        require_owner(request)
        try:
            return json.loads((s.public_home / ctx.PROJECTION_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"facts": [], "documents": []}

    return app


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "hash-passphrase":
        import getpass
        print(hash_passphrase(getpass.getpass("Owner passphrase: ")))
        sys.exit(0)
    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get("DOTV_HOST", "127.0.0.1"),
                port=int(os.environ.get("DOTV_PORT", "8780")), proxy_headers=True)
