import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotv.context import core  # noqa: E402

CANARY = "CANARY-PRIVATE-7f3a"
_spec = importlib.util.spec_from_file_location("dpc", ROOT / "dotv/plugins/dotv-public-context/__init__.py")
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)


@pytest.fixture
def homes(tmp_path):
    owner, public, ws = tmp_path / "owner", tmp_path / "public", tmp_path / "ws"
    (owner / "memories").mkdir(parents=True)
    ws.mkdir()
    (owner / "memories/MEMORY.md").write_text(f"blog is dotv.io\n§\nsecret {CANARY}", encoding="utf-8")
    (owner / "memories/USER.md").write_text("name is .v", encoding="utf-8")
    (ws / "bio.md").write_text(f"# Bio\n## School\nCS master\n## Love\n{CANARY}\n", encoding="utf-8")
    return owner, public, [ws]


def by_title(items, needle):
    return next(i for i in items if needle in i.title)


def test_scan_covers_sources(homes):
    owner, _, ws = homes
    kinds = {i.kind for i in core.scan(owner, ws)}
    assert {"memory", "user", "workspace"} <= kinds


def test_private_by_default(homes):
    owner, public, ws = homes
    proj = core.sync_public(owner, public, ws)
    assert proj["facts"] == [] and proj["documents"] == []
    assert CANARY not in (public / core.PROJECTION_FILE).read_text()


def test_only_approved_items_projected_and_originals_untouched(homes):
    owner, public, ws = homes
    before = (owner / "memories/MEMORY.md").read_text()
    items = core.scan(owner, ws)
    core.save_allowlist(owner, items, [by_title(items, "blog").id, by_title(items, "School").id])
    proj = core.sync_public(owner, public, ws)
    text = (public / core.PROJECTION_FILE).read_text()
    assert proj["facts"] == ["blog is dotv.io"] and len(proj["documents"]) == 1
    assert CANARY not in text
    assert (owner / "memories/MEMORY.md").read_text() == before


def test_edited_public_section_falls_back_to_private(homes):
    owner, public, ws = homes
    items = core.scan(owner, ws)
    core.save_allowlist(owner, items, [by_title(items, "School").id])
    (ws[0] / "bio.md").write_text(f"# Bio\n## School\nCS master {CANARY}\n", encoding="utf-8")
    items = core.scan(owner, ws)
    assert core.status(by_title(items, "School"), core.load_allowlist(owner)) == "changed"
    assert CANARY not in json.dumps(core.sync_public(owner, public, ws))


def test_unpublishable_items_cannot_be_approved(homes, tmp_path):
    owner, public, ws = homes
    import sqlite3
    con = sqlite3.connect(owner / "state.db")
    con.execute("CREATE TABLE sessions (id TEXT, source TEXT, title TEXT, started_at REAL, message_count INT)")
    con.execute("INSERT INTO sessions VALUES ('s1','cli',?,0,3)", (CANARY,))
    con.commit()
    items = core.scan(owner, ws)
    session = next(i for i in items if i.kind == "session")
    assert not session.publishable
    core.save_allowlist(owner, items, [session.id])
    assert CANARY not in json.dumps(core.sync_public(owner, public, ws))


def test_markdown_split_ignores_headings_in_code():
    parts = core.split_markdown("# A\nx\n```\n# not heading\n```\n## B\ny")
    assert [h for h, _ in parts] == ["A", "B"]


def test_plugin_renders_and_looks_up_projection(homes):
    owner, public, ws = homes
    items = core.scan(owner, ws)
    core.save_allowlist(owner, items, [by_title(items, "blog").id, by_title(items, "School").id])
    core.sync_public(owner, public, ws)
    proj = plugin.load_projection(public)
    section = plugin.render_section(proj)
    assert "blog is dotv.io" in section and CANARY not in section and len(section) <= plugin.SECTION_MAX
    assert "CS master" in plugin.lookup(proj, "school")
    assert plugin.lookup(proj, "zzz") == "No public document matches that query."
    assert plugin.load_projection(owner) == {}


def test_file_items_carry_absolute_path(homes):
    owner, _, ws = homes
    items = core.scan(owner, ws)
    assert by_title(items, "blog").path == str((owner / "memories/MEMORY.md").resolve())
    assert by_title(items, "School").path == str((ws[0] / "bio.md").resolve())
    assert all(Path(i.path).is_absolute() for i in items if i.path)
