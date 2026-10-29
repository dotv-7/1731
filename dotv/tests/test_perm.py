import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotv.perm import core as perm


@pytest.fixture
def owner(tmp_path):
    (tmp_path / "owner").mkdir()
    return tmp_path / "owner"


def test_defaults_and_locked_toolsets_never_saved(owner):
    assert perm.load(owner)["tiers"]["guest"] == perm.DEFAULT_TIERS["guest"]
    perm.save(owner, {"tiers": {"admin": ["terminal", "file", "web", "memory"], "user": [], "guest": ["web"]}})
    tiers = perm.load(owner)["tiers"]
    assert tiers == {"admin": ["web", "memory"], "user": [], "guest": ["web"]}


def test_members_code_lookup_tier_change_and_delete(owner):
    m, code = perm.add_member(owner, "小王", "user")
    assert code not in json.dumps(perm.load(owner))
    assert perm.find_member(owner, code)["id"] == m["id"] and perm.find_member(owner, "inv-wrong") is None
    assert perm.update_member(owner, m["id"], tier="blocked")
    assert perm.member_by_id(owner, m["id"])["tier"] == "blocked"
    assert perm.update_member(owner, m["id"], delete=True) and perm.find_member(owner, code) is None
    with pytest.raises(ValueError):
        perm.add_member(owner, "x", "owner")


def test_sync_writes_tier_file_and_narrows_profile_config(owner):
    yaml = pytest.importorskip("yaml")
    home = owner / "profiles/dotv-user"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text("model:\n  default: m\nplatform_toolsets:\n  api_server: [web, terminal]\n")
    perm.save(owner, {"tiers": {"admin": [], "user": ["web", "memory"], "guest": ["web"]}})
    done = perm.sync(owner, {"user": home, "admin": owner / "profiles/missing"})
    assert done == {"user": ["web", "memory"]}
    tier = json.loads((home / perm.TIER_FILE).read_text())
    assert tier["allowed_tools"] == ["memory", "web_extract", "web_search"]
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["platform_toolsets"]["api_server"] == ["web", "memory"] and cfg["model"]["default"] == "m"
    assert cfg["memory"]["memory_enabled"] is True and cfg["tools"]["tool_search"]["enabled"] == "off"
