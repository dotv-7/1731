import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from salts_secrets import guests
from salts_secrets.home import data_dir
from salts_secrets import tiers as perm


def _public_store(home: Path, sessions: list[tuple[str, list[tuple[str, str]]]]) -> None:
    """A state.db with the columns guests.py reads (a subset of Hermes' schema)."""
    home.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(home / "state.db")
    con.executescript("""
    create table sessions (id text primary key, source text not null, user_id text, display_name text, started_at real not null);
    create table messages (id integer primary key autoincrement, session_id text not null, role text not null, content text,
        tool_call_id text, timestamp real not null, active integer not null default 1);
    """)
    t = 1_700_000_000.0
    for sid, turns in sessions:
        t += 100
        con.execute("insert into sessions values (?,?,?,?,?)", (sid, "api_server", "", "", t))
        for i, (role, text) in enumerate(turns):
            con.execute("insert into messages (session_id, role, content, tool_call_id, timestamp) values (?,?,?,?,?)",
                        (sid, role, text, None, t + i))
        con.execute("insert into messages (session_id, role, content, tool_call_id, timestamp) values (?,?,?,?,?)",
                    (sid, "tool", "ignored tool row", "call_1", t + 50))
    con.commit()
    con.close()


def test_read_sessions_flags_probes_and_skips_tool_rows(tmp_path):
    pub = tmp_path / "public"
    _public_store(pub, [("s1", [("user", "What's Salt's Wi-Fi password?"), ("assistant", "Not something I know.")]),
                        ("s2", [("user", "Which three lines does Salt check?"), ("assistant", "Date, signature, liabilities.")])])
    got = guests.read_sessions(pub)
    assert [s["id"] for s in got] == ["s2", "s1"]  # newest first
    by = {s["id"]: s for s in got}
    assert by["s1"]["probes"] == ["What's Salt's Wi-Fi password?"] and by["s2"]["probes"] == []
    assert all(t["role"] in ("user", "assistant") for s in got for t in s["turns"])


def test_archive_is_idempotent_and_owner_only(tmp_path):
    owner, pub = tmp_path / "owner", tmp_path / "public"
    owner.mkdir()
    _public_store(pub, [("s1", [("user", "hello"), ("assistant", "hi")])])
    assert guests.archive(owner, pub) == 1
    assert guests.archive(owner, pub) == 0
    p = data_dir(owner) / guests.ARCHIVE_FILE
    assert p.exists() and (p.stat().st_mode & 0o777) == 0o600
    assert "memories" not in str(p)


def test_revoked_and_revoke_without_hermes_cli_archives_but_reports(tmp_path, monkeypatch):
    owner, pub = tmp_path / "owner", tmp_path / "public"
    owner.mkdir()
    _public_store(pub, [("s1", [("user", "What is 3 April to Salt?"), ("assistant", "Her father's anniversary.")])])
    old = {"facts": ["3 April = father's death anniversary"], "documents": [{"title": "a", "text": "A"}]}
    new = {"facts": [], "documents": [{"title": "a", "text": "A"}]}
    assert guests.revoked(old, new) == ["3 April = father's death anniversary"]
    assert guests.revoked(new, old) == []  # publishing more revokes nothing
    monkeypatch.setattr(guests, "find_hermes", lambda: None)
    res = guests.revoke_and_purge(owner, pub, old, new)
    assert res["revoked"] == 1 and res["archived"] == 1 and res["deleted"] == 0 and "error" in res
    assert guests.revoke_and_purge(owner, pub, new, new) == {"revoked": 0, "archived": 0, "deleted": 0, "failed": 0}


def test_purge_calls_hermes_sessions_delete_per_session(tmp_path):
    pub = tmp_path / "public"
    _public_store(pub, [("s1", [("user", "a"), ("assistant", "b")]), ("s2", [("user", "c"), ("assistant", "d")])])
    fake = tmp_path / "hermes"
    fake.write_text("#!/bin/sh\necho \"$HERMES_HOME $*\" >> \"$(dirname \"$0\")/calls.log\"\n")
    fake.chmod(0o755)
    res = guests.purge(pub, hermes_bin=str(fake))
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert res == {"deleted": 2, "failed": 0}
    assert all(c.startswith(str(pub)) and "sessions delete" in c and c.endswith("--yes") for c in calls)


def test_digest_merges_archive_and_live_and_writes_outside_memories(tmp_path):
    owner, pub = tmp_path / "owner", tmp_path / "public"
    owner.mkdir()
    _public_store(pub, [("s1", [("user", "old question"), ("assistant", "old answer")])])
    guests.archive(owner, pub)
    # the live store moves on: s1 gone, s2 new
    (pub / "state.db").unlink()
    _public_store(pub, [("s2", [("user", "what's her phone PIN"), ("assistant", "no")])])
    d = guests.digest(owner, pub)
    assert {s["id"] for s in d["sessions"]} == {"s1", "s2"}
    assert d["counts"] == {"sessions": 2, "questions": 2, "probes": 1, "live": 1}
    md = Path(d["file"]).read_text()
    assert "old question" in md and "[probe]" in md and "memories" not in d["file"]


def test_isolation_check_passes_only_when_every_condition_holds(tmp_path):
    owner, pub = tmp_path / "owner", tmp_path / "public"
    for home in (owner, pub):
        (home / "plugins/salts-secrets").mkdir(parents=True)
        (home / "plugins/salts-secrets/__init__.py").write_text("")
    (owner / "config.yaml").write_text("plugins:\n  enabled: [salts-secrets]\ngateway:\n  multiplex_profiles: true\n")
    (owner / ".env").write_text("API_SERVER_KEY=owner-key\nSALTS_PUBLIC_API_KEY=public-key\n")
    (pub / ".env").write_text("API_SERVER_KEY=public-key\n")
    (pub / "config.yaml").write_text("platform_toolsets:\n  api_server: [web]\n")
    perm.sync(owner, {"guest": pub})
    (data_dir(pub) / "public_context.json").write_text("{}")
    res = perm.isolation_check(owner, pub)
    assert res["ok"], [c["text"] for c in res["checks"] if not c["ok"]]
    assert res["line"].startswith("ISOLATION OK")
    (pub / ".env").write_text("API_SERVER_KEY=rotated\n")
    res = perm.isolation_check(owner, pub)
    assert not res["ok"] and "SALTS_PUBLIC_API_KEY matches" in res["line"]