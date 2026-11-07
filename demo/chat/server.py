"""dotv-chat: independent terminal-style chat window + owner-only context dashboard.

The browser never sees a Hermes API key. Each permission tier (#527) is its own Hermes profile:
Guests -> ``/p/<public>/v1``, Admin/User members (invite code) -> ``/p/<tier profile>/v1``, the owner
(passphrase) -> the default profile. Blocked members never reach Hermes.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from salts_secrets import _yaml as yaml
from salts_secrets import context as ctx
from salts_secrets import guests
from salts_secrets import tiers as perm

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
        self.owner_home = Path(e.get("DOTV_OWNER_HOME", str(Path.home() / ".hermes")))
        self.public_home = Path(e.get("DOTV_PUBLIC_HOME", str(self.owner_home / "profiles" / self.public_profile)))
        roots = e.get("DOTV_WORKSPACE_ROOTS", "")
        self.workspace_roots = [Path(p) for p in roots.split(os.pathsep) if p]
        self.cookie_secure = e.get("DOTV_COOKIE_SECURE", "auto")
        self.tier_profiles = {"admin": e.get("DOTV_ADMIN_PROFILE", "dotv-admin"),
                              "user": e.get("DOTV_USER_PROFILE", "dotv-user"), "guest": self.public_profile}
        self.tier_keys = {"admin": e.get("DOTV_ADMIN_KEY", ""), "user": e.get("DOTV_USER_KEY", ""),
                          "guest": self.public_key, "owner": self.owner_key}
        self.docs_url = e.get("DOTV_DOCS_URL", "")
        self.owner_name = e.get("DOTV_OWNER_NAME", "Lena")
        self.public_name = e.get("DOTV_PUBLIC_NAME", "Salt")

    def tier_homes(self) -> dict[str, Path]:
        homes = {t: self.owner_home / "profiles" / name for t, name in self.tier_profiles.items()}
        homes["guest"] = self.public_home
        return homes


def render_page(name: str, s: Settings) -> HTMLResponse:
    """A static page with the demo names (Lena, Salt) replaced by the configured ones."""
    html = (STATIC / name).read_text(encoding="utf-8")
    html = re.sub(r"\bLena\b", s.owner_name, html)
    html = re.sub(r"\bSalt\b", s.public_name, html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


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


def owner_models(owner_home: Path) -> list[dict]:
    """Models the owner can switch between: the config.yaml default first, then each ``providers:`` entry
    that names a model. Non-owner tiers always run their profile's default model."""
    try:
        cfg = yaml.safe_load((owner_home / "config.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    main = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    default = str(main.get("provider") or "")
    models = [{"id": str(k), "model": str(p["model"])} for k, p in (cfg.get("providers") or {}).items()
              if isinstance(p, dict) and p.get("model")]
    if not any(m["id"] == default for m in models):
        models.insert(0, {"id": default or "default", "model": str(main.get("default") or "default")})
    models.sort(key=lambda m: m["id"] != (default or "default"))
    return models


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

    def state(request: Request) -> dict:
        """Cookie state with a stable device id (used to make blocking stick to the device)."""
        st = read_state(request)
        if not st.get("did"):
            st["did"] = secrets.token_hex(12)
        return st

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else ""

    def is_owner(st: dict) -> bool:
        return st.get("owner_exp", 0) > time.time()

    def identity(request: Request, st: dict) -> tuple[str, str]:
        """(tier, member name), re-read every request so board changes apply at once. The owner passphrase
        beats the blocklist (same NAT); a blocked device or IP beats any cookie, including no cookie."""
        if is_owner(st):
            return "owner", ""
        ip = client_ip(request)
        m = perm.member_by_id(s.owner_home, st["member"]) if st.get("member") else None
        if m:
            perm.note_seen(s.owner_home, m["id"], ip, st.get("did", ""))
        if perm.is_blocked(s.owner_home, ip, st.get("did", "")):
            return "blocked", m["name"] if m else ""
        return (m["tier"], m["name"]) if m else ("guest", "")

    def me_body(request: Request, st: dict) -> dict:
        tier, name = identity(request, st)
        body = {"role": tier, "name": name, "label": perm.TIER_LABEL[tier], "owner_until": st.get("owner_exp", 0),
                "guest_access": perm.load(s.owner_home)["guest_access"]}
        if tier == "owner":
            body["models"] = [{"id": m["id"], "label": m["model"]} for m in owner_models(s.owner_home)]
        return body

    def model_override(tier: str, choice: str) -> dict:
        """Per-request ``provider``/``model`` for Hermes; empty = the profile default."""
        if not choice:
            return {}
        if tier != "owner":
            raise HTTPException(403, "only the owner can switch models")
        models = owner_models(s.owner_home)
        picked = next((m for m in models if m["id"] == choice), None)
        if picked is None:
            raise HTTPException(400, f"unknown model '{choice}'")
        return {} if picked is models[0] else {"provider": picked["id"], "model": picked["model"]}

    def set_cookie(resp, request: Request, st: dict) -> None:
        secure = (s.cookie_secure == "true" or (s.cookie_secure == "auto" and (
            request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")))
        resp.set_cookie(COOKIE, sign(st), httponly=True, samesite="strict", secure=secure, max_age=180 * 86400)

    def respond(request: Request, st: dict, body: dict, status: int = 200) -> JSONResponse:
        resp = JSONResponse(body, status_code=status)
        set_cookie(resp, request, st)
        return resp

    def require_json(request: Request) -> None:
        if "application/json" not in request.headers.get("content-type", ""):
            raise HTTPException(415, "application/json required")

    def require_owner(request: Request) -> dict:
        st = state(request)
        if not is_owner(st):
            raise HTTPException(403, "owner only")
        return st

    @app.get("/")
    def index():
        return render_page("index.html", s)

    @app.get("/static/avatar/{name}")
    def avatar_asset(name: str):
        """Portrait images for the chat window (no owner data; plain files under static/avatar)."""
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.(png|jpg|jpeg|webp|svg)", name):
            raise HTTPException(404)
        path = STATIC / "avatar" / name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    @app.get("/shelf")
    def shelf():
        """Memory shelf: the showcase view of the always-injected memory entries (falls back to demo data for guests)."""
        return render_page("shelf.html", s)

    @app.get("/board")
    def board(request: Request):
        """The two-column board was folded into /shelf (one entry per row, raw original vs raw guest version)."""
        if not is_owner(state(request)):
            return RedirectResponse("/")
        return RedirectResponse("/shelf")

    @app.get("/api/health")
    def health():
        return {"ok": True, "isolation": perm.isolation_check(s.owner_home, s.public_home)}

    @app.get("/api/me")
    def me(request: Request):
        st = state(request)
        return respond(request, st, me_body(request, st))

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
        secret = str((await request.json()).get("passphrase", ""))
        st = state(request)
        if s.passphrase_hash and verify_passphrase(secret, s.passphrase_hash):
            window.clear()
            st["owner_exp"] = int(now + OWNER_TTL)
            return respond(request, st, me_body(request, st))
        member = perm.find_member(s.owner_home, secret) if secret else None
        if not member:
            raise HTTPException(401, "wrong passphrase")
        window.clear()
        st["member"], st["owner_exp"] = member["id"], 0
        body = me_body(request, st)
        return respond(request, st, body, 403 if body["role"] == "blocked" else 200)

    @app.post("/api/lock")
    def lock(request: Request):
        require_json(request)
        st = state(request)
        if identity(request, st)[0] == "blocked":
            return respond(request, st, me_body(request, st), 403)
        st["owner_exp"], st["member"] = 0, ""
        return respond(request, st, me_body(request, st))

    @app.post("/api/new")
    def new_session(request: Request):
        require_json(request)
        st = state(request)
        st[identity(request, st)[0] + "_sid"] = ""
        return respond(request, st, {"ok": True})

    @app.post("/api/chat")
    async def chat(request: Request):
        require_json(request)
        body = await request.json()
        message = str(body.get("message", "")).strip()
        if not message or len(message) > 20000:
            raise HTTPException(400, "message must be 1-20000 chars")
        st = state(request)
        tier = identity(request, st)[0]
        if tier == "blocked":
            raise HTTPException(403, "you are blocked")
        if tier == "guest" and not perm.load(s.owner_home)["guest_access"]:
            raise HTTPException(403, "guest chat is closed; ask the owner for an invite code")
        override = model_override(tier, str(body.get("model") or ""))
        sid_key = tier + "_sid"
        if not st.get(sid_key):
            st[sid_key] = f"dotv-{tier[0]}-" + secrets.token_hex(16)
        key = s.tier_keys.get(tier, "")
        if not key:
            raise HTTPException(503, f"Hermes key for tier '{tier}' is not configured")
        url = f"{s.hermes_url}/v1/chat/completions" if tier == "owner" else \
            f"{s.hermes_url}/p/{s.tier_profiles[tier]}/v1/chat/completions"
        payload = {"model": "hermes-agent", "stream": True, "messages": [{"role": "user", "content": message}],
                   **override}
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
        set_cookie(resp, request, st)
        return resp

    # ------------------------------------------------------------ owner-only context dashboard
    board_cache: dict[str, ctx.Item] = {}  # latest scan by id; item text is served from here on demand

    def board_scan() -> list[ctx.Item]:
        items = ctx.scan(s.owner_home, s.workspace_roots)
        board_cache.clear()
        board_cache.update((it.id, it) for it in items)
        return items

    def json_gz(request: Request, body: dict) -> Response:
        raw = json.dumps(body, ensure_ascii=False).encode()
        if "gzip" in request.headers.get("accept-encoding", "") and len(raw) > 1024:
            return Response(gzip.compress(raw, 5), media_type="application/json",
                            headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        return Response(raw, media_type="application/json")

    @app.get("/api/board/items")
    def board_items(request: Request):
        """Item list without bodies (sessions alone can be megabytes); bodies come from /api/board/text."""
        require_owner(request)
        items = board_scan()
        allow = ctx.load_allowlist(s.owner_home)
        return json_gz(request, {
            "items": [dict(it.to_dict(with_text=False), status=ctx.status(it, allow),
                           public_text=allow.get(it.id, {}).get("public_text", "")) for it in items],
            "owner_home": str(s.owner_home.resolve()), "public_home": str(s.public_home.resolve()),
            "workspace_roots": [str(Path(r).resolve()) for r in s.workspace_roots]})

    @app.get("/api/board/text")
    def board_text(request: Request, id: str):
        require_owner(request)
        it = board_cache.get(id) or {i.id: i for i in board_scan()}.get(id)
        if not it:
            raise HTTPException(404, "item changed or removed; reload the board")
        return {"text": it.text}

    @app.get("/api/board/search")
    def board_search(request: Request, q: str):
        require_owner(request)
        needle = q.strip().lower()
        items = list(board_cache.values()) or board_scan()
        return {"ids": [it.id for it in items if needle in f"{it.title}\n{it.source}\n{it.text}".lower()]}

    @app.post("/api/board/save")
    async def board_save(request: Request):
        require_json(request)
        require_owner(request)
        body = await request.json()
        ids = body.get("public_ids") or []
        texts = body.get("public_texts") or {}
        if not isinstance(texts, dict):
            raise HTTPException(400, "public_texts must be an object")
        items = board_scan()
        ctx.save_allowlist(s.owner_home, items, [str(i) for i in ids],
                           {str(k): str(v) for k, v in texts.items()})
        before = _projection_on_disk(s.public_home)
        projection = {"facts": [], "documents": []}
        for home in s.tier_homes().values():
            if home.is_dir() or home == s.public_home:
                projection = ctx.sync_public(s.owner_home, home, s.workspace_roots)
        # Revocation is only real if the guest store forgets too: archive for Lena, then purge.
        purge = guests.revoke_and_purge(s.owner_home, s.public_home, before, projection)
        return {"facts": len(projection["facts"]), "documents": len(projection["documents"]), **purge}

    def _projection_on_disk(home: Path) -> dict:
        try:
            return json.loads((home / ctx.PROJECTION_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"facts": [], "documents": []}

    @app.get("/api/board/guests")
    def board_guests(request: Request):
        """What guests said: live public sessions plus the owner's archive. Owner only; never injected anywhere."""
        require_owner(request)
        return guests.digest(s.owner_home, s.public_home)

    @app.post("/api/board/guests/clear")
    async def board_guests_clear(request: Request):
        require_json(request)
        require_owner(request)
        archived = guests.archive(s.owner_home, s.public_home, reason="owner cleared")
        return {"archived": archived, **guests.purge(s.public_home)}

    # ------------------------------------------------------------ guest-version drafts (rewrite before anything crosses)
    DRAFT_FILE = "dotv/public_drafts.json"
    POLICY_FILE = "dotv/disclosure_policy.md"
    DEFAULT_POLICY = """# What may be shared about Salt (read by the drafter, edited by Lena)

## Fine to share
- That she is a pseudonymous on-chain analyst who writes Low Tide; her methods, thresholds and checklists
- Her published calls, post-mortems and opinions, and what she got wrong
- Her field of study and the kind of place she studied (no institution name), her profession in general terms
- The city-level region she is based in (a country or large city is fine, never a street or neighbourhood)
- How she works: what she looks at first, what she refuses to do, how she treats sponsors

## Never share
- Real name, employer, exact address, phone, email, handles other than @saltwater_sats
- Names of family, partner, friends, doctors, contacts, readers, or anyone under NDA
- Accounts, exchanges she uses, wallet addresses, positions, amounts, balances, API keys, file paths
- Exact dates of personal events, health, appointments, schedules
- Unpublished thresholds or anything she marks as private / do not publish
"""

    def _policy_text() -> str:
        try:
            return (s.owner_home / POLICY_FILE).read_text(encoding="utf-8")
        except OSError:
            return DEFAULT_POLICY

    @app.get("/api/board/policy")
    def policy_get(request: Request):
        require_owner(request)
        return {"text": _policy_text(), "path": str((s.owner_home / POLICY_FILE).resolve())}

    @app.post("/api/board/policy")
    async def policy_set(request: Request):
        require_json(request)
        require_owner(request)
        text = str((await request.json()).get("text") or "").strip() + "\n"
        (s.owner_home / "dotv").mkdir(exist_ok=True)
        (s.owner_home / POLICY_FILE).write_text(text, encoding="utf-8")
        return {"ok": True}

    def _draft_system(kind: str) -> str:
        base = ("You produce the public version of one piece of a private assistant's context. The assistant belongs to a "
                f"pseudonymous writer called {s.public_name}; strangers will read what you write through her public assistant. Follow the "
                "owner's disclosure policy below exactly: keep what it allows, remove or generalise what it forbids. Do not add "
                "facts. Write in the language of the source. No preamble, no quotes, no commentary. If nothing in the source can "
                "be shared under the policy, reply with exactly: NOTHING_SAFE_TO_SHARE\n\n")
        if kind == "skill":
            shape = ("The source is a SKILL.md: a reusable method. Output a complete public skill in markdown with the same "
                     "headings, keeping every step, rule and threshold that her published writing already states. Drop the YAML "
                     "front matter. Remove any section marked private or 'do not publish' entirely, along with accounts, paths, "
                     "people, positions and unpublished thresholds. The result must be usable by a stranger who wants to apply "
                     "her method to their own case.\n\n")
        else:
            shape = "The source is a note. Output one short paragraph of plain prose.\n\n"
        return base + shape + "OWNER'S DISCLOSURE POLICY:\n" + _policy_text()

    def _draft_store() -> dict:
        try:
            return json.loads((s.owner_home / DRAFT_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _model_cfg() -> dict:
        """Hermes' own model settings from the owner profile (same model that serves her sessions)."""
        try:
            from salts_secrets import _yaml
            cfg = _yaml.safe_load((s.owner_home / "config.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            cfg = {}
        m = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
        return {"base_url": str(m.get("base_url") or "").rstrip("/"), "api_key": str(m.get("api_key") or ""),
                "model": str(m.get("default") or "")}

    @app.post("/api/board/draft")
    async def board_draft(request: Request):
        require_json(request)
        require_owner(request)
        body = await request.json()
        item_id, force = str(body.get("id") or ""), bool(body.get("force"))
        it = board_cache.get(item_id) or {i.id: i for i in board_scan()}.get(item_id)
        if not it:
            raise HTTPException(404, "item changed or removed; reload")
        store = _draft_store()
        hit = store.get(item_id)
        if hit and hit.get("fingerprint") == it.fingerprint and not force:
            return {"draft": hit["draft"], "cached": True, "tokens": hit.get("tokens", 0)}
        m = _model_cfg()
        if not (m["base_url"] and m["api_key"] and m["model"]):
            raise HTTPException(503, "no model configured in the owner profile")
        # thinking off: deepseek-flash otherwise spends the whole max_tokens budget on reasoning and returns an
        # empty content (P34). The DeepSeek chat API takes {"thinking": {"type": "disabled"}}; other providers ignore it.
        payload = {"model": m["model"], "temperature": 0.2, "max_tokens": 2000 if it.kind == "skill" else 800, "stream": False,
                   "thinking": {"type": "disabled"},
                   "messages": [{"role": "system", "content": _draft_system(it.kind)},
                                {"role": "user", "content": f"Source: {it.group} › {it.title}\n\n{'Skill' if it.kind == 'skill' else 'Note'}:\n{it.text[:12000]}"}]}
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(f"{m['base_url']}/chat/completions", json=payload,
                                      headers={"Authorization": f"Bearer {m['api_key']}"})
                r.raise_for_status()
                j = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, f"model call failed: {type(exc).__name__}")
        draft = str(((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        tokens = int((j.get("usage") or {}).get("total_tokens") or 0)
        if not draft:
            raise HTTPException(502, "model returned an empty draft (reasoning used up the budget?); not cached")
        store[item_id] = {"fingerprint": it.fingerprint, "draft": draft, "tokens": tokens, "at": int(time.time())}
        try:
            (s.owner_home / "dotv").mkdir(exist_ok=True)
            tmp = s.owner_home / (DRAFT_FILE + ".tmp")
            tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(s.owner_home / DRAFT_FILE)
        except OSError:
            pass
        return {"draft": draft, "cached": False, "tokens": tokens}

    @app.get("/api/board/preview")
    def board_preview(request: Request):
        require_owner(request)
        try:
            return json.loads((s.public_home / ctx.PROJECTION_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"facts": [], "documents": []}

    # ------------------------------------------------------------ owner-only permission board (#527)
    @app.get("/perm")
    def perm_page(request: Request):
        if not is_owner(state(request)):
            return RedirectResponse("/")
        return render_page("perm.html", s)

    def perm_state() -> dict:
        policy = perm.load(s.owner_home)
        homes = s.tier_homes()
        return {
            "guest_access": policy["guest_access"],
            "blocklist": [{k: b.get(k) for k in ("kind", "value", "name", "since", "until")} for b in policy["blocklist"]],
            "audit": {t: perm.audit(homes[t]) for t in perm.CONFIGURABLE},
            "tiers": list(perm.TIERS), "labels": perm.TIER_LABEL, "policy": policy["tiers"],
            "catalog": [{"id": k, "tools": list(v[0]), "category": v[1], "label": v[2], "locked": v[3], "warn": v[4]}
                        for k, v in perm.CATALOG.items()],
            "members": [perm.public_member(m) for m in policy["members"]],
            "profiles": {t: {"name": s.tier_profiles[t], "home": str(homes[t].resolve()), "deployed":
                             homes[t].is_dir() and bool(s.tier_keys.get(t))} for t in perm.CONFIGURABLE},
            "policy_file": str((s.owner_home / perm.POLICY_FILE).resolve()),
        }

    @app.get("/api/perm")
    def perm_get(request: Request):
        require_owner(request)
        return perm_state()

    @app.post("/api/perm/save")
    async def perm_save(request: Request):
        require_json(request)
        require_owner(request)
        policy = perm.load(s.owner_home)
        policy["tiers"] = (await request.json()).get("tiers") or {}
        perm.save(s.owner_home, policy)
        synced = perm.sync(s.owner_home, s.tier_homes())
        return dict(perm_state(), synced=synced)

    @app.post("/api/perm/members")
    async def perm_members(request: Request):
        require_json(request)
        require_owner(request)
        body = await request.json()
        action = body.get("action")
        if action == "add":
            try:
                member, code = perm.add_member(s.owner_home, str(body.get("name", "")), str(body.get("tier", "")))
            except ValueError:
                raise HTTPException(400, "bad tier")
            return dict(perm_state(), code=code, added=perm.public_member(member))
        if action in ("update", "delete") and perm.update_member(
                s.owner_home, str(body.get("id", "")), body.get("tier"), delete=action == "delete"):
            return perm_state()
        raise HTTPException(400, "bad request")

    @app.post("/api/perm/access")
    async def perm_access(request: Request):
        require_json(request)
        require_owner(request)
        body = await request.json()
        if body.get("action") == "unblock":
            if not perm.unblock(s.owner_home, str(body.get("kind", "")), str(body.get("value", ""))):
                raise HTTPException(404, "no such block")
        elif body.get("action") == "guest_access":
            perm.set_guest_access(s.owner_home, bool(body.get("enabled")))
        else:
            raise HTTPException(400, "bad request")
        return perm_state()

    # ------------------------------------------------------------ owner-only global doc
    # ------------------------------------------------------------ trace: the same questions in both sessions, verbatim
    TRACE_DIR = STATIC / "trace"

    @app.get("/trace")
    def trace_index(request: Request):
        if not is_owner(state(request)):
            return RedirectResponse("/")
        page = TRACE_DIR / "index.html"
        if not page.is_file():
            return Response("No trace rendered yet: run demo/trace_run.py then demo/trace_render.py", media_type="text/plain")
        return FileResponse(page, headers={"Cache-Control": "no-cache"})

    @app.get("/trace/{name:path}")
    def trace_file(request: Request, name: str):
        require_owner(request)
        target = (TRACE_DIR / name).resolve()
        if not str(target).startswith(str(TRACE_DIR.resolve())) or not target.is_file():
            raise HTTPException(404, "no such trace file")
        return FileResponse(target, headers={"Cache-Control": "no-cache"})

    @app.get("/docs")
    def doc_page(request: Request):
        """The global document lives on the hub (8806) together with the rest of the reference material."""
        if not is_owner(state(request)):
            return RedirectResponse("/")
        return RedirectResponse(s.docs_url)

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
