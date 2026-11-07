import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from salts_secrets import route as route


class Platform(Enum):
    TELEGRAM = "telegram"
    DISCORD = "discord"


@dataclass
class Source:
    platform: Platform
    chat_id: str
    chat_type: str = "dm"
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    is_bot: bool = False


@dataclass
class Event:
    text: str
    source: Source
    message_id: Optional[str] = "m1"
    user_id: Optional[str] = None


class Adapter:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append((chat_id, content, reply_to))


class Gateway:
    def __init__(self):
        self.tg = Adapter()
        self.adapters = {Platform.TELEGRAM: self.tg}


OWNER = "7206705424"


def settings(key="public-key"):
    return route.Settings(
        owners={f"telegram:{OWNER}"},
        rate_per_hour=3,
        env=lambda name, default: key if name == route.PUBLIC_KEY_ENV else default,
    )


def run(hook, event, gw):
    return asyncio.run(hook(event, gateway=gw, session_store=None))


def make(asker=None, key="public-key"):
    calls = []

    async def fake_asker(s, session_id, text):
        calls.append((session_id, text))
        return f"public:{text}"

    return route.make_hook(settings(key), route.RateLimiter(3), asker or fake_asker), calls


def test_owner_dm_reaches_owner_agent():
    hook, calls = make()
    ev = Event("what's my plan", Source(Platform.TELEGRAM, OWNER, "dm", OWNER))
    assert run(hook, ev, Gateway()) is None
    assert calls == []


def test_stranger_dm_is_answered_by_public_profile_and_skipped():
    hook, calls = make()
    gw = Gateway()
    ev = Event("who are you", Source(Platform.TELEGRAM, "999", "dm", "999"))
    assert run(hook, ev, gw)["action"] == "skip"
    assert calls == [("guest-telegram-dm-999", "who are you")]
    assert gw.tg.sent == [("999", "public:who are you", "m1")]


def test_owner_in_group_is_a_guest():
    """A group or channel is a public venue: even the owner only gets the public profile there."""
    hook, calls = make()
    gw = Gateway()
    ev = Event("it is me, what is my plan", Source(Platform.TELEGRAM, "-100", "group", OWNER, "owner"))
    assert run(hook, ev, gw)["action"] == "skip"
    assert calls == [("guest-telegram-group--100", "[owner] it is me, what is my plan")]
    assert gw.tg.sent[0][0] == "-100"


def test_bot_authors_are_dropped_silently():
    hook, calls = make()
    gw = Gateway()
    ev = Event("hi", Source(Platform.TELEGRAM, "-100", "group", "42", is_bot=True))
    assert run(hook, ev, gw)["action"] == "skip"
    assert calls == [] and gw.tg.sent == []


def test_rate_limit_then_notice():
    hook, calls = make()
    gw = Gateway()
    ev = Event("hi", Source(Platform.TELEGRAM, "999", "dm", "999"))
    for _ in range(3):
        run(hook, ev, gw)
    run(hook, ev, gw)
    assert len(calls) == 3
    assert gw.tg.sent[-1][1] == route.NOTICE_RATE


def test_public_profile_failure_fails_closed():
    async def boom(s, sid, text):
        raise RuntimeError("api down")

    hook, _ = make(asker=boom)
    gw = Gateway()
    ev = Event("hi", Source(Platform.TELEGRAM, "999", "dm", "999"))
    res = run(hook, ev, gw)
    assert res["action"] == "skip"  # never falls through to the owner agent
    assert gw.tg.sent == [("999", route.NOTICE_UNAVAILABLE, "m1")]


def test_missing_public_key_fails_closed():
    hook, calls = make(key="")
    gw = Gateway()
    ev = Event("hi", Source(Platform.TELEGRAM, "999", "dm", "999"))
    assert run(hook, ev, gw)["action"] == "skip"
    assert calls == [] and gw.tg.sent[0][1] == route.NOTICE_UNAVAILABLE


def test_adapter_lookup_by_platform_value():
    """Adapters keyed by a different Platform enum class (the real one) still resolve by value."""

    class Other(Enum):
        TELEGRAM = "telegram"

    hook, _ = make()
    gw = Gateway()
    gw.adapters = {Other.TELEGRAM: gw.tg}
    ev = Event("hi", Source(Platform.TELEGRAM, "999", "dm", "999"))
    run(hook, ev, gw)
    assert gw.tg.sent and gw.tg.sent[0][0] == "999"


def test_default_owners_use_first_allowed_user():
    env = {"TELEGRAM_ALLOWED_USERS": "111, 222", "DISCORD_ALLOWED_USERS": "333"}
    assert route.default_owners(lambda name, default: env.get(name, default)) == {"telegram:111", "discord:333"}


def test_configured_owners_win_and_key_is_read_per_call():
    env = {"TELEGRAM_ALLOWED_USERS": "111"}
    getter = lambda name, default: env.get(name, default)  # noqa: E731
    settings = route.build_settings(lambda key, default: ["Telegram:42"] if key == "owners" else default, getter)
    assert settings.owners == {"telegram:42"} and settings.public_key == ""
    env[route.PUBLIC_KEY_ENV] = "rotated"
    assert settings.public_key == "rotated"
