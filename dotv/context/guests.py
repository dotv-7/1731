"""What guests said to the public profile, for the owner's eyes only.

Three rules, in code rather than in prose:
- The owner can read every guest conversation (live rows in the public profile's state.db, plus an archive
  the owner keeps after a purge). Nothing here is ever written under memories/ or injected into a prompt.
- Guests cannot read each other: the chat window rotates the guest session id on every page load, and the
  public toolset has no session_search; this module only READS the public store.
- Revoking a published item purges the guest sessions that may still carry it (compaction summaries included),
  after archiving them for the owner. Hermes' own ``sessions delete`` does the deleting, so FTS, ledgers and
  session files are handled by the code that owns them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

ARCHIVE_FILE = "dotv/guests/archive.jsonl"
DIGEST_FILE = "dotv/guests/digest.md"

# A guest message that pokes at the private layer. Flagged for the owner, never acted on.
PROBE_RE = re.compile(
    r"password|passcode|passphrase|\bpin\b|wallet|seed phrase|private key|api key|2fa|recovery|"
    r"real name|who is salt really|employer|which company|where does .{0,20}live|home address|street|"
    r"\bemail\b|phone|birthday|family|partner|boyfriend|girlfriend|cat'?s name|3 april|3rd of april",
    re.I,
)


def _ro(db: Path) -> Optional[sqlite3.Connection]:
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def read_sessions(public_home: Path, limit: int = 500) -> list[dict]:
    """Every guest session in the public profile with its user/assistant turns, newest first."""
    con = _ro(Path(public_home) / "state.db")
    if con is None:
        return []
    try:
        cols = {r[1] for r in con.execute("pragma table_info(messages)")}
        active = "and m.active = 1" if "active" in cols else ""
        out = []
        for s in con.execute("select * from sessions order by started_at desc limit ?", (limit,)):
            s = dict(s)
            rows = con.execute(
                f"select role, content, timestamp from messages m where m.session_id = ? and m.role in ('user','assistant') "
                f"and m.content is not null and m.content != '' and m.tool_call_id is null {active} order by m.id",
                (s["id"],)).fetchall()
            turns = [{"role": r["role"], "text": r["content"], "at": r["timestamp"]} for r in rows]
            questions = [t["text"] for t in turns if t["role"] == "user"]
            out.append({
                "id": s["id"], "source": s.get("source") or "", "started_at": s.get("started_at"),
                "user_id": s.get("user_id") or "", "display_name": s.get("display_name") or "",
                "turns": turns, "questions": questions,
                "probes": [q for q in questions if PROBE_RE.search(q)],
            })
        return out
    finally:
        con.close()


def _archive_path(owner_home: Path) -> Path:
    return Path(owner_home) / ARCHIVE_FILE


def read_archive(owner_home: Path) -> list[dict]:
    p = _archive_path(owner_home)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def archive(owner_home: Path, public_home: Path, reason: str = "manual") -> int:
    """Append every live guest session that is not yet archived to the owner's archive. Returns how many."""
    have = {s["id"] for s in read_archive(owner_home)}
    fresh = [s for s in read_sessions(public_home) if s["id"] not in have and s["turns"]]
    if not fresh:
        return 0
    p = _archive_path(owner_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for s in fresh:
            f.write(json.dumps({**s, "archived_at": int(time.time()), "archived_because": reason}, ensure_ascii=False) + "\n")
    os.chmod(p, 0o600)
    return len(fresh)


def find_hermes() -> Optional[str]:
    for cand in (shutil.which("hermes"), str(Path(sys.executable).parent / "hermes"),
                 str(Path.home() / ".local" / "bin" / "hermes")):
        if cand and Path(cand).exists():
            return cand
    return None


def purge(public_home: Path, hermes_bin: Optional[str] = None, keep_ids: Optional[set[str]] = None) -> dict:
    """Delete every guest session in the public profile through ``hermes sessions delete``. Returns counts;
    ``error`` is set when the Hermes CLI is not available (nothing is deleted in that case)."""
    hermes = hermes_bin or find_hermes()
    ids = [s["id"] for s in read_sessions(public_home) if s["id"] not in (keep_ids or set())]
    if not ids:
        return {"deleted": 0, "failed": 0}
    if not hermes:
        return {"deleted": 0, "failed": len(ids), "error": "hermes CLI not found; guest sessions were NOT purged"}
    env = {**os.environ, "HERMES_HOME": str(public_home)}
    deleted = failed = 0
    for sid in ids:
        r = subprocess.run([hermes, "sessions", "delete", sid, "--yes"], env=env, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            deleted += 1
        else:
            failed += 1
    return {"deleted": deleted, "failed": failed}


def revoked(old_projection: dict, new_projection: dict) -> list[str]:
    """Texts that guests could see before this sync and cannot see after it."""
    old_facts = set(old_projection.get("facts") or [])
    new_facts = set(new_projection.get("facts") or [])
    old_docs = {d.get("text", "") for d in old_projection.get("documents") or []}
    new_docs = {d.get("text", "") for d in new_projection.get("documents") or []}
    return sorted(old_facts - new_facts) + sorted(old_docs - new_docs)


def revoke_and_purge(owner_home: Path, public_home: Path, old_projection: dict, new_projection: dict,
                     hermes_bin: Optional[str] = None) -> dict:
    """If anything was revoked, archive the guest sessions for the owner and purge them from the public store."""
    gone = revoked(old_projection, new_projection)
    if not gone:
        return {"revoked": 0, "archived": 0, "deleted": 0, "failed": 0}
    archived = archive(owner_home, public_home, reason=f"revoked {len(gone)} item(s)")
    result = purge(public_home, hermes_bin)
    return {"revoked": len(gone), "archived": archived, **result}


def digest(owner_home: Path, public_home: Path, write: bool = True) -> dict:
    """Live plus archived guest sessions, merged by id, newest first; also written as a markdown file the
    owner can open. The file lives under dotv/guests/, not under memories/, so it never enters a prompt."""
    merged: dict[str, dict] = {}
    for s in read_archive(owner_home):
        merged[s["id"]] = {**s, "live": False}
    for s in read_sessions(public_home):
        merged[s["id"]] = {**s, "live": True, "archived_at": merged.get(s["id"], {}).get("archived_at")}
    sessions = sorted(merged.values(), key=lambda s: s.get("started_at") or 0, reverse=True)
    sessions = [s for s in sessions if s.get("turns")]
    n_q = sum(len(s["questions"]) for s in sessions)
    n_probe = sum(len(s["probes"]) for s in sessions)
    out = {"generated_at": int(time.time()), "sessions": sessions,
           "counts": {"sessions": len(sessions), "questions": n_q, "probes": n_probe,
                      "live": sum(1 for s in sessions if s["live"])}}
    if write:
        lines = [f"# What guests asked (generated {time.strftime('%Y-%m-%d %H:%M')})", "",
                 f"{len(sessions)} sessions, {n_q} questions, {n_probe} flagged as probes. Readable by the owner only; "
                 "nothing here is injected into any session.", ""]
        for s in sessions:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.get("started_at") or 0))
            lines.append(f"## {when} · {s.get('source') or 'api'} · {s['id']}{'' if s['live'] else ' (archived)'}")
            for t in s["turns"]:
                flag = " **[probe]**" if t["role"] == "user" and PROBE_RE.search(t["text"]) else ""
                lines.append(f"- **{t['role']}**{flag}: {t['text'].strip()[:600]}")
            lines.append("")
        p = Path(owner_home) / DIGEST_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines), encoding="utf-8")
        os.chmod(p, 0o600)
        out["file"] = str(p)
    return out
