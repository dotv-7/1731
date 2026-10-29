import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("dotv_guest_gate", ROOT / "plugins/dotv-guest-gate/__init__.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

DENIED = ["terminal", "process_manage", "execute_code", "read_file", "write_file", "patch", "search_files",
          "memory", "session_search", "skills_list", "skill_view", "skill_manage", "browser_navigate",
          "computer_use", "cronjob_manage", "delegate_task", "send_message", "todo_list", "kanban_create",
          "mcp_github_create_issue", "", "WEB_SEARCH"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(gate.ENV_ALLOWED, raising=False)


@pytest.mark.parametrize("tool", DENIED)
def test_denied_tools_blocked(tool):
    assert gate.on_pre_tool_call(tool_name=tool, args={})["action"] == "block"


@pytest.mark.parametrize("tool", ["web_search", "web_extract", "clarify", "image_generate", "text_to_speech", "dotv_public_lookup"])
def test_allowed_tools_pass(tool):
    assert gate.on_pre_tool_call(tool_name=tool, args={"query": "x"}) is None


@pytest.mark.parametrize("url,blocked", [
    ("https://example.com/a.png", False), ("http://example.com/a.png", False),
    ("/opt/data/memories/MEMORY.md", True), ("file:///etc/passwd", True), ("~/photo.jpg", True), (None, True),
])
def test_vision_only_remote_urls(url, blocked):
    result = gate.on_pre_tool_call(tool_name="vision_analyze", args={"image_url": url})
    assert (result is not None) is blocked


def test_env_can_only_narrow(monkeypatch):
    monkeypatch.setenv(gate.ENV_ALLOWED, "web_search,terminal")
    assert gate.on_pre_tool_call(tool_name="web_search", args={}) is None
    assert gate.on_pre_tool_call(tool_name="terminal", args={})["action"] == "block"
    assert gate.on_pre_tool_call(tool_name="web_extract", args={})["action"] == "block"


def test_fail_closed_on_error(monkeypatch):
    monkeypatch.setattr(gate, "check", lambda *a: 1 / 0)
    assert gate.on_pre_tool_call(tool_name="web_search", args={})["action"] == "block"


def test_register_hooks_pre_tool_call():
    calls = []
    gate.register(type("Ctx", (), {"register_hook": lambda self, n, f: calls.append((n, f))})())
    assert calls == [("pre_tool_call", gate.on_pre_tool_call)]


def test_profile_template_matches_gate():
    cfg = yaml.safe_load((ROOT / "profiles/public/config.yaml").read_text())
    assert set(cfg["platform_toolsets"]["api_server"]) == {"web", "vision", "image_gen", "tts", "clarify", "dotv_public"}
    assert cfg["plugins"]["enabled"] == ["dotv-guest-gate", "dotv-public-context"]
    assert cfg["security"]["allow_private_urls"] is False
    assert cfg["memory"]["provider"] == "" and cfg["memory"]["nudge_interval"] == 0
    manifest = yaml.safe_load((ROOT / "plugins/dotv-guest-gate/plugin.yaml").read_text())
    assert manifest["name"] == "dotv-guest-gate" and "pre_tool_call" in manifest["provides_hooks"]
