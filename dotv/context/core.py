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
DATA_SUFFIXES = {".json", ".jsonl", ".yaml", ".yml", ".csv", ".toml", ".log", ".html", ".xml"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", ".mypy_cache", ".pytest_cache"}
SKIP_FILE_SUFFIXES = (".lock", ".pid", ".sock", "-wal", "-shm", "-journal", ".pyc")
# HERMES_HOME paths that are config, secrets, caches, runtime state or dotv add-ons, not the owner's content.
HOME_EXCLUDE = {".env", "auth.json", "config.yaml", "logs", "cache", "plugins", "profiles", "dotv", "runtime",
                "installs", "state", "source-checks", "plugin-update-checks", "platforms", "pairing", "checkpoints",
                "sandboxes", "bin", "web_dist", "backups/config", "gateway_state.json", "gateway-starts.log",
                "install_id", "models_dev_cache.json", "models_dev_cache.etag", ".skills_prompt_snapshot.json",
                "runs_idempotency.db", "skills/.bundled_manifest", "cron/ticker_heartbeat",
                "cron/ticker_last_success"}
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


def _ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _skip_file(name: str) -> bool:
    return name.endswith(SKIP_FILE_SUFFIXES) or name == ".DS_Store"


def _walk(root: Path, exclude: set[str], stop_dirs: set[Path], seen: set[Path]) -> Iterable[tuple[Path, str]]:
    """Yield ``(abs_path, rel)`` for every file under ``root``, so newly created paths show up automatically."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        keep = []
        for d in sorted(dirnames):
            full = here / d
            rel = full.relative_to(root).as_posix()
            if d in SKIP_DIRS or rel in exclude or full.resolve() in stop_dirs:
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            p = (here / name)
            rel = p.relative_to(root).as_posix()
            if _skip_file(name) or rel in exclude or p.resolve() in seen or not p.is_file():
                continue
            seen.add(p.resolve())
            count += 1
            if count > MAX_FILES:
                return
            yield p, rel


def _size(path: Path) -> str:
    n = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def _session_items(db: Path, limit: int = 500, per_session: int = 30) -> list[Item]:
    try:
        con = _ro(db)
        cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        title = "title" if "title" in cols else ("display_name" if "display_name" in cols else "NULL")
        rows = con.execute(
            f"SELECT id, {title}, source, started_at, message_count FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit,)).fetchall()
        msgs: dict[str, list[str]] = {}
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='messages'").fetchone():
            for sid, role, content in con.execute(
                    "SELECT session_id, role, content FROM messages WHERE role IN ('user','assistant') "
                    "AND content IS NOT NULL AND content != '' ORDER BY id"):
                bucket = msgs.setdefault(sid, [])
                if len(bucket) < per_session:
                    bucket.append(f"[{role}] {content[:400]}")
        con.close()
    except sqlite3.Error:
        return []
    items = []
    for sid, t, src, started, n in rows:
        when = time.strftime("%Y-%m-%d", time.localtime(started or 0))
        label = f"{when} · {t or '(无标题)'} · {src} · {n or 0} 条消息"
        body = "\n\n".join(msgs.get(sid, [])) or label
        items.append(_item("session", "历史会话 · state.db", f"state.db/{sid}", "", label, body,
                           publishable=False, note="历史会话第一版不能公开（私密与公开内容混在一起）",
                           path=str(db.resolve())))
    return items


def _kanban_items(db: Path) -> list[Item]:
    try:
        con = _ro(db)
        tasks = con.execute("SELECT id, title, body, status FROM tasks ORDER BY created_at DESC").fetchall()
        comments: dict[str, list[str]] = {}
        for tid, author, body in con.execute("SELECT task_id, author, body FROM task_comments ORDER BY id"):
            comments.setdefault(tid, []).append(f"[{author}] {body}")
        con.close()
    except sqlite3.Error:
        return _database_items(db, "kanban.db")
    return [_item("kanban", "看板任务 · kanban.db", f"kanban.db/{tid}", "", f"{title} · {st}",
                  "\n\n".join([title, body or "", *comments.get(tid, [])]), publishable=False,
                  note="任务记录不能公开", path=str(db.resolve())) for tid, title, body, st in tasks]


def _strings(node, out: list[str], budget: int = 4000) -> None:
    if sum(map(len, out)) > budget:
        return
    if isinstance(node, str):
        if node.strip():
            out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in ("text", "content", "input", "output", "instructions", "arguments"):
                _strings(v, out, budget)
    elif isinstance(node, list):
        for v in node:
            _strings(v, out, budget)


def _response_items(db: Path) -> list[Item]:
    try:
        con = _ro(db)
        rows = con.execute("SELECT response_id, data, accessed_at FROM responses ORDER BY accessed_at DESC LIMIT 500").fetchall()
        con.close()
    except sqlite3.Error:
        return _database_items(db, "response_store.db")
    items = []
    for rid, data, at in rows:
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = data
        texts: list[str] = []
        _strings(parsed, texts)
        when = time.strftime("%Y-%m-%d", time.localtime(at or 0))
        items.append(_item("response", "API 对话记录 · response_store.db", f"response_store.db/{rid}", "",
                           f"{when} · {rid}", "\n\n".join(texts)[:4000] or data[:4000], publishable=False,
                           note="对话记录不能公开", path=str(db.resolve())))
    return items


def _cron_items(path: Path) -> list[Item]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _data_items(path, "cron/jobs.json", "定时任务 · cron")
    jobs = data.get("jobs", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    return [_item("cron", "定时任务 · cron", f"cron/jobs.json#{j.get('id', i)}", "",
                  str(j.get("name") or j.get("id") or f"job {i}"), json.dumps(j, ensure_ascii=False, indent=1)[:4000],
                  publishable=False, note="定时任务不能公开", path=str(path.resolve()))
            for i, j in enumerate(jobs) if isinstance(j, dict)]


def _database_items(db: Path, rel: str, group: str = "") -> list[Item]:
    try:
        con = _ro(db)
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        lines = []
        for n in names:
            rows = con.execute('SELECT count(*) FROM "' + n.replace('"', '""') + '"').fetchone()[0]
            lines.append(f"{n}: {rows} 行")
        con.close()
    except sqlite3.Error:
        lines = ["（无法读取）"]
    return [_item("database", group or _group_for(rel), rel, "", f"{Path(rel).name} · 数据库 · {_size(db)}",
                  "\n".join(lines), publishable=False, note="数据库只展示结构", path=str(db.resolve()))]


def _data_items(path: Path, rel: str, group: str) -> list[Item]:
    text = _read(path) or ""
    return [_item("data", group, rel, "", f"{Path(rel).name} · {_size(path)}", text[:4000] or "（空）",
                  publishable=False, note="结构化数据不能公开", path=str(path.resolve()))]


def _file_item(path: Path, rel: str, group: str) -> list[Item]:
    stat = path.stat()
    return [_item("file", group, rel, "", f"{Path(rel).name} · {_size(path)}",
                  f"{path.resolve()}\n{_size(path)} · 修改于 {time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime))}",
                  publishable=False, note="非文本文件只展示路径", path=str(path.resolve()))]


def _group_for(rel: str, prefix: str = "数据目录") -> str:
    parts = rel.split("/")
    return f"{prefix} · {parts[0]}" if len(parts) > 1 else f"{prefix} · 根目录"


def _generic_items(path: Path, rel: str, group: str, kind: str = "workspace") -> list[Item]:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES or path.name == ".cursorrules":
        text = _read(path)
        return _doc_items(kind, group, rel, text, path) if text else _file_item(path, rel, group)
    if suffix in DATA_SUFFIXES:
        return _data_items(path, rel, group)
    if _is_sqlite(path):
        return _database_items(path, rel, group)
    return _file_item(path, rel, group)


def _home_item(home: Path, path: Path, rel: str) -> list[Item]:
    if rel in ("memories/MEMORY.md", "memories/USER.md"):
        return []  # parsed entry by entry in _memory_items
    if rel == "SOUL.md":
        return _doc_items("soul", "人设 · SOUL.md", rel, _read(path) or "", path)
    if rel.startswith("skills/") and path.name == "SKILL.md":
        text = _read(path)
        return [_item("skill", "Skills", rel, "", path.parent.name, text, path=str(path.resolve()))] if text else []
    special = {"state.db": _session_items, "kanban.db": _kanban_items, "response_store.db": _response_items,
               "cron/jobs.json": _cron_items}
    if rel in special:
        return special[rel](path)
    group = "Skills" if rel.startswith("skills/") else _group_for(rel)
    return _generic_items(path, rel, group)


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


def _bundled_skill_dirs(home: Path) -> set[Path]:
    """Official skills synced in by Hermes (tools/skills_sync.py manifest); they are not the owner's content."""
    try:
        names = {line.split(":", 1)[0].strip() for line in
                 (home / "skills" / ".bundled_manifest").read_text(encoding="utf-8").splitlines() if line.strip()}
    except OSError:
        return set()
    return {p.parent.resolve() for p in (home / "skills").rglob("SKILL.md") if p.parent.name in names}


def scan(owner_home: Path, workspace_roots: list[Path]) -> list[Item]:
    """Everything under HERMES_HOME and the workspace roots, minus config/secrets/caches/add-ons (HOME_EXCLUDE)."""
    owner_home = Path(owner_home)
    home_abs = owner_home.resolve()
    seen: set[Path] = set()
    items = _memory_items(owner_home)
    bundled = _bundled_skill_dirs(owner_home)
    if bundled:
        items.append(_item("skill", "Skills", "skills/.bundled_manifest", "", f"官方内置 skills：{len(bundled)} 个（已折叠）",
                           "\n".join(sorted(d.name for d in bundled)), publishable=False,
                           note="Hermes 自带，不是你的内容", path=str((owner_home / "skills").resolve())))
    if owner_home.is_dir():
        skills = (owner_home / "skills").resolve()
        exclude = HOME_EXCLUDE | {f"skills/{d.parent.relative_to(skills).as_posix()}/DESCRIPTION.md"
                                  for d in bundled if d.parent != skills}
        for path, rel in _walk(owner_home, exclude, bundled, seen):
            items += _home_item(owner_home, path, rel)
    for root in (Path(r) for r in workspace_roots):
        if root.is_dir() and root.resolve() != home_abs:
            for path, rel in _walk(root, set(), {home_abs}, seen):
                items += _generic_items(path, f"{root.name}/{rel}", _group_for(rel, f"Workspace · {root.name}"))
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
