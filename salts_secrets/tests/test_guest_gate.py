import json
from pathlib import Path

import pytest

from salts_secrets import _yaml as yaml
from salts_secrets import gate, tiers
from salts_secrets.home import data_dir

ROOT = Path(gate.__file__).resolve().parent
DENIED = [
    "terminal",
    "process_manage",
    "execute_code",
    "read_file",
    "write_file",
    "patch",
    "search_files",
    "memory",
    "session_search",
    "skills_list",
    "skill_view",
    "skill_manage",
    "browser_exec",
    "computer_use",
    "cronjob_manage",
    "delegate_task",
    "send_message",
    "todo_list",
    "kanban_create",
    "mcp_github_create_issue",
    "",
    "WEB_SEARCH",
]


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setattr("salts_secrets.home.current", lambda: tmp_path)


def _tier(tmp_path, content: str):
    path = data_dir(tmp_path) / gate.TIER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_tier_file_widens_within_ceiling_only(tmp_path):
    _tier(tmp_path, json.dumps({"tier": "user", "allowed_tools": ["memory", "web_search", "terminal", "read_file"]}))
    assert gate.on_pre_tool_call(tool_name="memory", args={}) is None
    for tool in ("terminal", "read_file", "web_extract"):
        assert gate.on_pre_tool_call(tool_name=tool, args={})["action"] == "block"


def test_corrupt_tier_file_blocks_everything(tmp_path):
    _tier(tmp_path, "{not json")
    assert gate.on_pre_tool_call(tool_name="web_search", args={})["action"] == "block"


def test_ceiling_excludes_owner_machine_tools():
    locked = {t for spec in tiers.CATALOG.values() if spec[3] for t in spec[0]}
    assert not (gate.CEILING & locked)
    assert set(tiers.tools_for(tiers.SAFE_TOOLSETS)) <= gate.CEILING


@pytest.mark.parametrize("tool", DENIED)
def test_denied_tools_blocked(tool):
    assert gate.on_pre_tool_call(tool_name=tool, args={})["action"] == "block"


@pytest.mark.parametrize(
    "tool", ["web_search", "web_extract", "clarify", "image_generate", "text_to_speech", "public_notes_lookup"]
)
def test_allowed_tools_pass(tool):
    assert gate.on_pre_tool_call(tool_name=tool, args={"query": "x"}) is None


@pytest.mark.parametrize(
    "url,blocked",
    [
        ("https://example.com/a.png", False),
        ("http://example.com/a.png", False),
        ("/home/someone/.hermes/memories/MEMORY.md", True),
        ("file:///etc/passwd", True),
        ("~/photo.jpg", True),
        (None, True),
    ],
)
def test_vision_only_remote_urls(url, blocked):
    result = gate.on_pre_tool_call(tool_name="vision_analyze", args={"image_url": url})
    assert (result is not None) is blocked


def test_fail_closed_on_error(monkeypatch):
    monkeypatch.setattr(gate, "check", lambda *a: 1 / 0)
    assert gate.on_pre_tool_call(tool_name="web_search", args={})["action"] == "block"


def test_register_hooks_pre_tool_call():
    calls = []
    gate.register(type("Ctx", (), {"register_hook": lambda self, n, f: calls.append((n, f))})())
    assert calls == [("pre_tool_call", gate.on_pre_tool_call)]


def test_profile_template_matches_gate():
    cfg = yaml.safe_load((ROOT / "profile/config.yaml").read_text())
    assert set(cfg["platform_toolsets"]["api_server"]) == {
        "web",
        "vision",
        "image_gen",
        "tts",
        "clarify",
        "public_notes",
    }
    assert cfg["security"]["allow_private_urls"] is False
    assert cfg["tools"]["tool_search"]["enabled"] == "off"
    assert cfg["memory"]["provider"] == "" and cfg["memory"]["nudge_interval"] == 0
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    assert manifest["name"] == "salts-secrets"
    assert {"pre_tool_call", "pre_gateway_dispatch"} <= set(manifest["provides_hooks"])


def _registrations(monkeypatch, home, settings):
    import salts_secrets

    monkeypatch.setattr("salts_secrets.home.current", lambda: home)
    calls = []
    ctx = type("Ctx", (), {
        "get_config": lambda self, key, default=None: settings.get(key, default),
        "__getattr__": lambda self, name: lambda *a, **k: calls.append(name),
    })()
    salts_secrets.register(ctx)
    return calls


@pytest.mark.parametrize("home_rel,settings,public", [
    ("owner", {}, False),
    ("owner/profiles/public", {}, True),
    ("owner/profiles/strangers", {"public_profile": "strangers"}, True),
    ("owner/profiles/anything", {"role": "public"}, True),
    ("owner/profiles/public", {"role": "owner"}, False),
])
def test_role_follows_settings_then_profile_name(monkeypatch, tmp_path, home_rel, settings, public):
    calls = _registrations(monkeypatch, tmp_path / home_rel, settings)
    assert ("register_hook" in calls and "register_tool" in calls) is public
    assert ("register_cli_command" in calls) is not public
