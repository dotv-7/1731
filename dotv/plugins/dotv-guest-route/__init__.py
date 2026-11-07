"""dotv-guest-route: route every non-owner message to the public profile before the owner agent sees it.

Rule (one line): only the owner's private DM is an owner conversation. Everything else is a guest
conversation and is executed by the public profile, which physically lacks the owner's memory, files and
tools. The owner agent never receives those messages (``pre_gateway_dispatch`` -> ``skip``).

Fail-closed: any failure on the guest path ends in ``skip`` (plus a short notice when possible). The hook
never raises, because the gateway treats a raised hook as "fall through to normal dispatch", which would
hand the message to the owner agent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from typing import Any, Optional

log = logging.getLogger("dotv.guest_route")

PLATFORM_ENV_PREFIX = {
    "telegram": "TELEGRAM", "discord": "DISCORD", "slack": "SLACK", "whatsapp": "WHATSAPP",
    "signal": "SIGNAL", "matrix": "MATRIX", "mattermost": "MATTERMOST",
}
NOTICE_UNAVAILABLE = "The guest entrance is temporarily unavailable. Please try again in a minute."
NOTICE_RATE = "You have reached the guest message limit for this hour. Please come back later."
NOTICE_TEXT_ONLY = "I can only read text here."
REPLY_MAX = 3500


class Settings:
    def __init__(self, owners: list[str], public_profile: str, rate_per_hour: int,
                 api_base: str, public_key: str):
        self.owners = {o.strip().lower() for o in owners if o and o.strip()}
        self.public_profile = public_profile or "public"
        self.rate_per_hour = max(1, int(rate_per_hour or 20))
        self.api_base = api_base.rstrip("/")
        self.public_key = public_key


def owners_from_env(env: dict[str, str]) -> set[str]:
    """#527 convention: the first id in <PLATFORM>_ALLOWED_USERS is the owner. DOTV_OWNER_IDS adds more."""
    out: set[str] = set()
    for platform, prefix in PLATFORM_ENV_PREFIX.items():
        raw = env.get(f"{prefix}_ALLOWED_USERS", "")
        first = raw.split(",")[0].strip() if raw else ""
        if first:
            out.add(f"{platform}:{first}".lower())
    for item in env.get("DOTV_OWNER_IDS", "").split(","):
        if item.strip():
            out.add(item.strip().lower())
    return out


def platform_name(platform: Any) -> str:
    return str(getattr(platform, "value", platform) or "").lower()


def is_owner_dm(event: Any, owners: set[str]) -> bool:
    src = getattr(event, "source", None)
    if src is None or getattr(src, "chat_type", "") != "dm":
        return False
    uid = str(getattr(src, "user_id", None) or getattr(event, "user_id", None) or "")
    return bool(uid) and f"{platform_name(src.platform)}:{uid}".lower() in owners


def guest_session_id(event: Any) -> str:
    src = event.source
    p = platform_name(src.platform)
    if src.chat_type == "dm":
        return f"dotv-guest-{p}-dm-{src.user_id}"
    return f"dotv-guest-{p}-{src.chat_type}-{src.chat_id}"


def guest_text(event: Any) -> str:
    text = (getattr(event, "text", "") or "").strip()
    src = event.source
    if src.chat_type == "dm" or not text:
        return text
    who = getattr(src, "user_name", None) or getattr(event, "user_name", None) or src.user_id or "guest"
    return f"[{who}] {text}"


class RateLimiter:
    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = now or time.time()
        q = self.hits[key]
        while q and q[0] < now - 3600:
            q.popleft()
        if len(q) >= self.per_hour:
            return False
        q.append(now)
        return True


async def ask_public_profile(settings: Settings, session_id: str, text: str) -> str:
    """One stateless turn against the public profile; Hermes keeps the conversation by session id."""
    import httpx  # Hermes ships httpx; imported lazily so the module loads without it in tests

    url = f"{settings.api_base}/p/{settings.public_profile}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {settings.public_key}", "X-Hermes-Session-Id": session_id}
    payload = {"model": "hermes-agent", "messages": [{"role": "user", "content": text}]}
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    reply = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return reply.strip()


def make_hook(settings: Settings, limiter: RateLimiter, asker=ask_public_profile):
    async def send(gateway: Any, event: Any, content: str) -> None:
        src = event.source
        adapters = getattr(gateway, "adapters", {}) or {}
        adapter = adapters.get(src.platform)
        if adapter is None:  # adapters are keyed by the Platform enum; fall back to matching by value
            for key, value in adapters.items():
                if platform_name(key) == platform_name(src.platform):
                    adapter = value
                    break
        if adapter is None:
            log.warning("dotv-guest-route: no adapter for %s, reply dropped", platform_name(src.platform))
            return
        await adapter.send(src.chat_id, content[:REPLY_MAX], reply_to=getattr(event, "message_id", None))

    async def hook(event: Any, gateway: Any = None, session_store: Any = None, **_: Any) -> Optional[dict]:
        try:
            if is_owner_dm(event, settings.owners):
                return None  # the only path that reaches the owner agent
        except Exception as exc:  # noqa: BLE001 - classification failure must not fall through
            log.warning("dotv-guest-route: classify failed (%s); treating as guest", exc)
        try:
            src = event.source
            if getattr(src, "is_bot", False):
                return {"action": "skip", "reason": "dotv-guest-route: bot author"}
            text = guest_text(event)
            if not text:
                await send(gateway, event, NOTICE_TEXT_ONLY)
                return {"action": "skip", "reason": "dotv-guest-route: no text"}
            who = f"{platform_name(src.platform)}:{src.user_id or src.chat_id}"
            if not limiter.allow(who):
                await send(gateway, event, NOTICE_RATE)
                return {"action": "skip", "reason": "dotv-guest-route: rate limited"}
            if not settings.public_key:
                await send(gateway, event, NOTICE_UNAVAILABLE)
                return {"action": "skip", "reason": "dotv-guest-route: no public key"}
            reply = await asker(settings, guest_session_id(event), text)
            await send(gateway, event, reply or "(no reply)")
            return {"action": "skip", "reason": "dotv-guest-route: answered by public profile"}
        except Exception as exc:  # noqa: BLE001
            log.warning("dotv-guest-route: guest path failed: %s", exc)
            try:
                await send(gateway, event, NOTICE_UNAVAILABLE)
            except Exception:  # noqa: BLE001
                pass
            return {"action": "skip", "reason": "dotv-guest-route: failed closed"}

    return hook


def build_settings(get_config=None, env: Optional[dict[str, str]] = None) -> Settings:
    env = os.environ if env is None else env
    cfg = (lambda k, d: d) if get_config is None else get_config
    owners = set(cfg("owners", []) or []) | owners_from_env(env)
    host = env.get("API_SERVER_HOST", "127.0.0.1") or "127.0.0.1"
    port = env.get("API_SERVER_PORT", "8642") or "8642"
    return Settings(owners=list(owners), public_profile=cfg("public_profile", "public"),
                    rate_per_hour=cfg("guest_rate_per_hour", 20), api_base=f"http://{host}:{port}",
                    public_key=env.get("DOTV_PUBLIC_KEY", ""))


def register(ctx) -> None:
    settings = build_settings(lambda k, d: ctx.get_config(k, default=d))
    if not settings.owners:
        log.warning("dotv-guest-route: no owner identity configured; every platform message is a guest")
    log.info("dotv-guest-route: owners=%s public_profile=%s", sorted(settings.owners), settings.public_profile)
    ctx.register_hook("pre_gateway_dispatch", make_hook(settings, RateLimiter(settings.rate_per_hour)))
