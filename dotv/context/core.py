from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

ENTRY_DELIMITER = "\n§\n"  # tools/memory_tool_store.py
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".mdc"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", "sessions", "logs",
             "profiles", "plugins", "dotv", "memories", "skills", "checkpoints", "cron", "audio_cache",
             "image_cache", "browser_screenshots", "sandboxes", "bin", "web_dist"}
MAX_FILE_BYTES = 1_000_000
MAX_FILES = 3000
ALLOWLIST_FILE = "dotv/public_allowlist.json"
PROJECTION_FILE = "dotv/public_context.json"
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:24]


@dataclass
class Item:
    id: str
    kind: str             # memory | user | soul | workspace | skill | session | provider | plugin
    group: str            # dashboard grouping label
    source: str           # path relative to its root, or a descriptor
    title: str
    text: str
    fingerprint: str
    publishable: bool = True
    note: str = ""
    path: str = ""        # absolute path of the backing file, "" for non-file sources
    meta: dict = field(default_factory=dict)

    def to_dict(self, with_text: bool = True) -> dict:
        d = asdict(self)
        if not with_text:
            d.pop("text")
        return d


def _item(kind: str, group: str, source: str, locator: str, title: str, text: str, **kw) -> Item:
    fp = fingerprint(text)
    item_id = hashlib.sha256(f"{kind}\0{source}\0{locator}".encode()).hexdigest()[:20]
    return Item(id=item_id, kind=kind, group=group, source=source, title=title, text=text.strip(),
                fingerprint=fp, **kw)


def _read(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _memory_items(home: Path) -> list[Item]:
    items = []
    for name, kind, group in (("MEMORY.md", "memory", "Memory · MEMORY.md"), ("USER.md", "user", "Memory · USER.md")):
        raw = _read(home / "memories" / name)
        if not raw:
            continue
        for entry in (e.strip() for e in raw.split(ENTRY_DELIMITER)):
            if entry:
                # Entries have no stable name; their content is their identity (an edited entry is a new one).
                first = entry.splitlines()[0]
                items.append(_item(kind, group, f"memories/{name}", fingerprint(entry), first[:80], entry,
                                   path=str((home / "memories" / name).resolve())))
    return items


def split_markdown(text: str) -> list[tuple[str, str]]:
    """``[(heading, body)]`` split at #/##/### headings; text before the first heading is "(开头)"."""
    sections: list[tuple[str, list[str]]] = [("(开头)", [])]
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m:
            sections.append((m.group(2), [line]))
        else:
            sections[-1][1].append(line)
    out, seen = [], {}
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        seen[heading] = seen.get(heading, 0) + 1
        out.append((heading if seen[heading] == 1 else f"{heading} ({seen[heading]})", body))
    return out


def _doc_items(kind: str, group: str, rel: str, text: str, path: Path) -> list[Item]:
    abs_path = str(path.resolve())
    if Path(rel).suffix.lower() in {".md", ".markdown", ".mdc"}:
        parts = split_markdown(text)
        if len(parts) > 1:
            return [_item(kind, group, rel, h, f"{Path(rel).name} › {h}", body, path=abs_path) for h, body in parts]
    return [_item(kind, group, rel, "", Path(rel).name, text, path=abs_path)] if text.strip() else []


def _iter_text_files(root: Path) -> Iterable[Path]:
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix.lower() in TEXT_SUFFIXES or name == ".cursorrules":
                count += 1
                if count > MAX_FILES:
                    return
                yield p


def _workspace_items(roots: list[Path]) -> list[Item]:
    items = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in _iter_text_files(root):
            text = _read(p)
            if text:
                rel = f"{root.name}/{p.relative_to(root).as_posix()}"
                items += _doc_items("workspace", f"Workspace · {root.name}", rel, text, p)
    return items


def _skill_items(home: Path) -> list[Item]:
    items = []
    skills = home / "skills"
    if skills.is_dir():
        for p in sorted(skills.rglob("SKILL.md")):
            text = _read(p)
            if text:
                rel = p.relative_to(home).as_posix()
                items.append(_item("skill", "Skills", rel, "", p.parent.name, text, path=str(p.resolve())))
    return items


def _session_items(home: Path, limit: int = 500) -> list[Item]:
    db = home / "state.db"
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        title = "title" if "title" in cols else ("display_name" if "display_name" in cols else "NULL")
        rows = con.execute(
            f"SELECT id, {title}, source, started_at, message_count FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit,)).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    items = []
    for sid, t, src, started, n in rows:
        when = time.strftime("%Y-%m-%d", time.localtime(started or 0))
        label = f"{when} · {t or '(无标题)'} · {src} · {n or 0} 条消息"
        it = _item("session", "历史会话", f"state.db/{sid}", "", label, label,
                   publishable=False, note="历史会话第一版不能公开（私密与公开内容混在一起）",
                   path=str(db.resolve()))
        items.append(it)
    return items


def _config(home: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _runtime_items(home: Path) -> list[Item]:
    items = []
    provider = ((_config(home).get("memory") or {}).get("provider") or "").strip()
    if provider:
        items.append(_item("provider", "外部 memory / 插件", f"memory.provider={provider}", "",
                           f"外部 memory provider：{provider}", provider, publishable=False,
                           note="访客 profile 不接外部 memory"))
    plugins = home / "plugins"
    if plugins.is_dir():
        for p in sorted(x for x in plugins.iterdir() if x.is_dir() and not x.name.startswith(".")):
            items.append(_item("plugin", "外部 memory / 插件", f"plugins/{p.name}", "", f"插件：{p.name}",
                               p.name, publishable=False, note="插件动态上下文不会进入访客会话",
                               path=str(p.resolve())))
    return items


def scan(owner_home: Path, workspace_roots: list[Path]) -> list[Item]:
    owner_home = Path(owner_home)
    items = _memory_items(owner_home)
    soul = _read(owner_home / "SOUL.md")
    if soul:
        items += _doc_items("soul", "人设 · SOUL.md", "SOUL.md", soul, owner_home / "SOUL.md")
    items += _workspace_items([Path(r) for r in workspace_roots])
    items += _skill_items(owner_home)
    items += _session_items(owner_home)
    items += _runtime_items(owner_home)
    return items


# ---------------------------------------------------------------- allowlist

def _atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_allowlist(owner_home: Path) -> dict[str, dict]:
    try:
        data = json.loads((Path(owner_home) / ALLOWLIST_FILE).read_text(encoding="utf-8"))
        return {e["id"]: e for e in data.get("entries", []) if isinstance(e, dict) and e.get("id")}
    except (OSError, ValueError):
        return {}


def save_allowlist(owner_home: Path, items: list[Item], public_ids: Iterable[str]) -> dict[str, dict]:
    wanted = set(public_ids)
    entries = {
        it.id: {"id": it.id, "kind": it.kind, "source": it.source, "title": it.title,
                "fingerprint": it.fingerprint, "approved_at": int(time.time())}
        for it in items if it.id in wanted and it.publishable
    }
    _atomic_write(Path(owner_home) / ALLOWLIST_FILE,
                  json.dumps({"version": 1, "entries": list(entries.values())}, ensure_ascii=False, indent=2))
    return entries


def status(item: Item, allowlist: dict[str, dict]) -> str:
    """``public`` (approved, unchanged) | ``changed`` (approved, content edited since → treated as private) |
    ``private``."""
    entry = allowlist.get(item.id)
    if not entry or not item.publishable:
        return "private"
    return "public" if entry.get("fingerprint") == item.fingerprint else "changed"


def build_projection(items: list[Item], allowlist: dict[str, dict]) -> dict:
    """Only items approved with an unchanged fingerprint; everything else stays out (private by default)."""
    public = [it for it in items if status(it, allowlist) == "public"]
    return {
        "version": 1,
        "generated_at": int(time.time()),
        "facts": [it.text for it in public if it.kind in ("memory", "user")],
        "documents": [{"title": it.title, "text": it.text} for it in public if it.kind not in ("memory", "user")],
    }


def sync_public(owner_home: Path, public_home: Path, workspace_roots: list[Path]) -> dict:
    items = scan(owner_home, workspace_roots)
    projection = build_projection(items, load_allowlist(owner_home))
    _atomic_write(Path(public_home) / PROJECTION_FILE, json.dumps(projection, ensure_ascii=False, indent=2), 0o644)
    return projection
