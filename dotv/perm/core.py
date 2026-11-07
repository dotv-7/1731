"""Gateway permission tiers (upstream #527): who may do what.

Owner-side source of truth is ``<owner_home>/dotv/permissions.json`` (per-tier toolsets + members).
``sync`` writes each tier profile's ``dotv/tier.json`` (read by dotv-guest-gate) and narrows its
``platform_toolsets`` so the model only sees allowed tools. Owner = default profile, unrestricted;
Blocked never reaches Hermes. Non-owner tiers share the owner's machine, so anything that can read
the filesystem or act as the owner stays locked regardless of the policy file.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from pathlib import Path
from typing import Iterable, Optional

from dotv import _yaml as yaml

POLICY_FILE = "dotv/permissions.json"
TIER_FILE = "dotv/tier.json"
ISOLATED_CWD = "dotv/cwd"
BLOCK_TTL = 90 * 86400
SEEN_MAX = 5
GATE_PLUGIN = "dotv-guest-gate"
CONTEXT_PLUGIN = "dotv-public-context"
# Files Hermes auto-loads into the system prompt from the working directory (agent/prompt_builder.py).
CONTEXT_FILE_NAMES = (".hermes.md", "HERMES.md", "AGENTS.md", "AGENTS.override.md", "agents.md", "CLAUDE.md",
                      ".cursorrules", ".cursor")
TIERS = ("owner", "admin", "user", "guest", "blocked")
CONFIGURABLE = ("admin", "user", "guest")
TIER_LABEL = {"owner": "Owner", "admin": "Admin", "user": "User", "guest": "Guest", "blocked": "Blocked"}

# toolset -> (tools, category, label, lock reason for non-owner tiers or "", shared-state warning or "")
CATALOG: dict[str, tuple[tuple[str, ...], str, str, str, str]] = {
    "web": (("web_search", "web_extract"), "Web", "Search and fetch pages", "", ""),
    "x_search": (("x_search",), "Web", "X/Twitter search", "", ""),
    "vision": (("vision_analyze",), "Images and video", "Look at images (URLs only)", "", ""),
    "video": (("video_analyze",), "Images and video", "Watch video", "", ""),
    "image_gen": (("image_generate",), "Generate", "Generate images", "", "spends your API budget"),
    "video_gen": (("video_generate", "xai_video_edit", "xai_video_extend"), "Generate", "Generate video", "", "spends your API budget"),
    "tts": (("text_to_speech",), "Generate", "Read aloud", "", ""),
    "clarify": (("clarify",), "Conversation", "Ask a clarifying question", "", ""),
    "dotv_public": (("dotv_public_lookup",), "Conversation", "Look up public notes", "", ""),
    "todo": (("todo_list",), "Conversation", "To-do list", "", ""),
    "memory": (("memory",), "Memory", "Memory (the tier's own)", "", "shared by everyone in the tier"),
    "session_search": (("session_search",), "Memory", "Search past sessions (the tier's own)", "", "members of the tier can search each other's sessions"),
    "skills": (("skills_list", "skill_view", "skill_manage"), "Skills", "Skills (the tier's own)", "", "shared by everyone in the tier"),
    "terminal": (("terminal", "process_manage"), "Terminal and code", "Terminal", "can read any file on your machine", ""),
    "code_execution": (("execute_code",), "Terminal and code", "Run code", "can read any file on your machine", ""),
    "file": (("read_file", "write_file", "patch", "search_files"), "Files", "Read and write files", "can read your private files", ""),
    "browser": (("browser_exec", "browser_cdp", "browser_dialog", "browser_vault"), "Browser", "Browser (4 tools, v0.23.0)", "can use sites you are signed in to and open local files", ""),
    "computer_use": (("computer_use",), "Browser", "Computer use", "can drive your desktop", ""),
    "cronjob": (("cronjob_manage",), "Scheduling", "Scheduled jobs", "runs as you, indefinitely", ""),
    "delegation": (("delegate_task",), "Scheduling", "Delegate sub-tasks", "sub-tasks bypass the gate", ""),
    "kanban": (("kanban_create",), "Scheduling", "Kanban tasks", "can see your tasks", ""),
    "connections": (("manage_connections",), "Outbound", "Account connections", "can use your accounts", ""),
    "messaging": (("send_message",), "Outbound", "Send messages", "sends messages in your name", ""),
}
SAFE_TOOLSETS = frozenset(k for k, v in CATALOG.items() if not v[3])
DEFAULT_TIERS = {
    "admin": ["web", "x_search", "vision", "video", "image_gen", "tts", "clarify", "dotv_public", "todo",
              "memory", "session_search", "skills"],
    "user": ["web", "vision", "image_gen", "tts", "clarify", "dotv_public", "todo", "memory"],
    "guest": ["web", "vision", "image_gen", "tts", "clarify", "dotv_public"],
}


def tools_for(toolsets: Iterable[str]) -> list[str]:
    return sorted({t for ts in toolsets if ts in SAFE_TOOLSETS for t in CATALOG[ts][0]})


def _clean(toolsets: Iterable[str]) -> list[str]:
    return [k for k in CATALOG if k in set(toolsets) and k in SAFE_TOOLSETS]


def _live_blocks(entries, now: float) -> list[dict]:
    return [b for b in entries or [] if isinstance(b, dict) and b.get("kind") in ("ip", "device")
            and isinstance(b.get("value"), str) and b["value"] and b.get("until", 0) > now]


def load(owner_home: Path) -> dict:
    try:
        data = json.loads((Path(owner_home) / POLICY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    tiers = data.get("tiers") if isinstance(data.get("tiers"), dict) else {}
    return {"tiers": {t: _clean(tiers.get(t, DEFAULT_TIERS[t])) for t in CONFIGURABLE},
            "members": [m for m in data.get("members") or [] if isinstance(m, dict) and m.get("tier") in TIERS[1:]],
            "blocklist": _live_blocks(data.get("blocklist"), time.time()),
            "guest_access": data.get("guest_access", True) is not False}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def save(owner_home: Path, policy: dict) -> dict:
    clean = {"tiers": {t: _clean((policy.get("tiers") or {}).get(t, [])) for t in CONFIGURABLE},
             "members": policy.get("members") or [],
             "blocklist": _live_blocks(policy.get("blocklist"), time.time()),
             "guest_access": policy.get("guest_access", True) is not False}
    _write(Path(owner_home) / POLICY_FILE, clean)
    return clean


# ------------------------------------------------------------------ members (invite codes)
def _hash(code: str, salt: bytes) -> str:
    return hashlib.scrypt(code.encode(), salt=salt, n=2 ** 14, r=8, p=1).hex()


def add_member(owner_home: Path, name: str, tier: str) -> tuple[dict, str]:
    if tier not in TIERS[1:]:
        raise ValueError("bad tier")
    policy = load(owner_home)
    code = "inv-" + secrets.token_urlsafe(12)
    salt = secrets.token_bytes(16)
    member = {"id": secrets.token_hex(6), "name": name.strip()[:40] or "unnamed", "tier": tier,
              "salt": salt.hex(), "code_hash": _hash(code, salt), "created": int(time.time())}
    policy["members"].append(member)
    save(owner_home, policy)
    return member, code


def update_member(owner_home: Path, member_id: str, tier: Optional[str] = None, delete: bool = False) -> bool:
    policy = load(owner_home)
    found = next((m for m in policy["members"] if m.get("id") == member_id), None)
    if not found or (tier is not None and tier not in TIERS[1:]):
        return False
    if delete:
        policy["members"].remove(found)
    elif tier:
        found["tier"] = tier
        if tier == "blocked":
            _block_member(policy, found)
        else:
            policy["blocklist"] = [b for b in policy["blocklist"] if b.get("member") != member_id]
    save(owner_home, policy)
    return True


# ------------------------------------------------------------------ blocking that survives a cleared cookie
# Membership lives in the browser cookie, so a blocked member could otherwise clear it and come back as a
# Guest. Every device id and IP a member was seen from is remembered; blocking turns them into blocklist
# entries that the chat server checks before anything else. IP entries expire (shared NAT / mobile IPs).
def _add_block(policy: dict, kind: str, value: str, member: dict) -> None:
    if not value or any(b["kind"] == kind and b["value"] == value for b in policy["blocklist"]):
        return
    policy["blocklist"].append({"kind": kind, "value": value, "member": member.get("id", ""),
                                "name": member.get("name", ""), "since": int(time.time()),
                                "until": int(time.time() + BLOCK_TTL)})


def _block_member(policy: dict, member: dict) -> None:
    for ip in member.get("seen_ips") or []:
        _add_block(policy, "ip", ip, member)
    for did in member.get("seen_devices") or []:
        _add_block(policy, "device", did, member)


_SHARED_NETS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                                        "100.64.0.0/10", "fc00::/7"))


def blockable_ip(ip: str) -> bool:
    """Only public client IPs are blocked: loopback/LAN/container addresses usually mean a reverse proxy that
    is not trusted for X-Forwarded-For, and blocking it would lock out every visitor."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_loopback or a.is_link_local or a.is_unspecified or any(a in n for n in _SHARED_NETS))


def note_seen(owner_home: Path, member_id: str, ip: str, device: str) -> None:
    """Remember where a member connects from (last few IPs/devices); a blocked member's new ones get blocked too."""
    policy = load(owner_home)
    m = next((x for x in policy["members"] if x.get("id") == member_id), None)
    if m is None:
        return
    changed = False
    for field, value in (("seen_ips", ip if blockable_ip(ip) else ""), ("seen_devices", device)):
        seen = [v for v in m.get(field) or [] if isinstance(v, str)]
        if value and value not in seen:
            m[field] = (seen + [value])[-SEEN_MAX:]
            changed = True
    if m.get("tier") == "blocked":
        before = len(policy["blocklist"])
        _block_member(policy, m)
        changed = changed or len(policy["blocklist"]) != before
    if changed:
        save(owner_home, policy)


def is_blocked(owner_home: Path, ip: str, device: str) -> Optional[dict]:
    return next((b for b in load(owner_home)["blocklist"]
                 if (b["kind"] == "ip" and ip and b["value"] == ip) or (b["kind"] == "device" and device
                                                                       and b["value"] == device)), None)


def unblock(owner_home: Path, kind: str, value: str) -> bool:
    policy = load(owner_home)
    keep = [b for b in policy["blocklist"] if not (b["kind"] == kind and b["value"] == value)]
    if len(keep) == len(policy["blocklist"]):
        return False
    policy["blocklist"] = keep
    save(owner_home, policy)
    return True


def set_guest_access(owner_home: Path, enabled: bool) -> None:
    policy = load(owner_home)
    policy["guest_access"] = bool(enabled)
    save(owner_home, policy)


def find_member(owner_home: Path, code: str) -> Optional[dict]:
    for m in load(owner_home)["members"]:
        try:
            if hmac.compare_digest(_hash(code, bytes.fromhex(m["salt"])), m["code_hash"]):
                return m
        except (KeyError, ValueError, TypeError):
            continue
    return None


def member_by_id(owner_home: Path, member_id: str) -> Optional[dict]:
    return next((m for m in load(owner_home)["members"] if m.get("id") == member_id), None)


def public_member(m: dict) -> dict:
    return {k: m.get(k) for k in ("id", "name", "tier", "created")}


# ------------------------------------------------------------------ sync into tier profiles
def sync(owner_home: Path, tier_homes: dict[str, Path]) -> dict[str, list[str]]:
    """Write tier.json + narrow platform_toolsets for every tier profile that exists."""
    policy = load(owner_home)
    done = {}
    for tier, home in tier_homes.items():
        home = Path(home)
        if tier not in CONFIGURABLE or not home.is_dir():
            continue
        toolsets = policy["tiers"][tier]
        _write(home / TIER_FILE, {"tier": tier, "toolsets": toolsets, "allowed_tools": tools_for(toolsets)})
        _narrow_config(home / "config.yaml", toolsets)
        done[tier] = toolsets
    return done


def isolated_cwd(home: Path) -> Path:
    """Empty working directory for a tier profile, so Hermes finds no project context files
    (AGENTS.md, .hermes.md, CLAUDE.md, ...) to inject. Without an explicit ``terminal.cwd`` Hermes falls
    back to MESSAGING_CWD or the home directory, which can be the owner's workspace."""
    path = Path(home) / ISOLATED_CWD
    path.mkdir(parents=True, exist_ok=True)
    return path


def _narrow_config(path: Path, toolsets: list[str]) -> None:
    if not path.exists():
        return
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg.setdefault("platform_toolsets", {})["api_server"] = list(toolsets)
    mem = cfg.setdefault("memory", {})
    mem["memory_enabled"] = mem["user_profile_enabled"] = "memory" in toolsets
    mem["provider"] = ""
    cfg.setdefault("tools", {}).setdefault("tool_search", {})["enabled"] = "off"
    cfg.setdefault("terminal", {})["cwd"] = str(isolated_cwd(path.parent).resolve())
    cfg.setdefault("security", {})["allow_private_urls"] = False
    plugins = cfg.setdefault("plugins", {})
    enabled = [p for p in plugins.get("enabled") or [] if isinstance(p, str)]
    plugins["enabled"] = enabled + [p for p in (GATE_PLUGIN, CONTEXT_PLUGIN) if p not in enabled]
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def _context_files_near(cwd: Path) -> list[str]:
    """Context files Hermes would load for this cwd: in cwd itself, plus the git-root chain if cwd is in a repo."""
    cwd = cwd.resolve()
    root = next((p for p in (cwd, *cwd.parents) if (p / ".git").exists()), None)
    dirs = [cwd] if root is None else [cwd, *[p for p in cwd.parents if p.is_relative_to(root)]]
    found = [str(d / n) for d in dirs for n in CONTEXT_FILE_NAMES if (d / n).exists()]
    if root is not None:
        found.append(f"{root} is a git repository (AGENTS.md chain walks up to it)")
    return found


def audit(home: Path) -> list[str]:
    """Isolation problems of a non-owner tier profile; an empty list means the hard isolation is in place."""
    home = Path(home)
    if not home.is_dir():
        return ["profile does not exist"]
    path = home / "config.yaml"
    if not path.exists():
        return ["config.yaml is missing"]
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [f"config.yaml cannot be parsed: {exc}"]
    problems = []
    toolsets = ((cfg.get("platform_toolsets") or {}).get("api_server")) or []
    bad = [t for t in toolsets if t not in SAFE_TOOLSETS]
    if bad:
        problems.append("toolsets that are owner-only are enabled: " + ", ".join(map(str, bad)))
    if str(((cfg.get("tools") or {}).get("tool_search") or {}).get("enabled", "")).lower() not in ("off", "false"):
        problems.append("tools.tool_search is on (its tool_call bridge bypasses the tool list)")
    if (cfg.get("memory") or {}).get("provider"):
        problems.append("an external memory provider is configured")
    if (cfg.get("security") or {}).get("allow_private_urls") is not False:
        problems.append("security.allow_private_urls is not false (web tools could reach the LAN)")
    enabled = (cfg.get("plugins") or {}).get("enabled") or []
    if GATE_PLUGIN not in enabled or not (home / "plugins" / GATE_PLUGIN / "__init__.py").exists():
        problems.append(f"tool gate plugin {GATE_PLUGIN} is not installed or not enabled")
    cwd = (cfg.get("terminal") or {}).get("cwd")
    if not cwd or str(cwd) in (".", "auto", "cwd"):
        problems.append("terminal.cwd is not set: Hermes falls back to MESSAGING_CWD or the home directory, whose AGENTS.md etc. would enter the guest prompt")
    elif not Path(cwd).is_dir():
        problems.append(f"terminal.cwd does not exist: {cwd}")
    else:
        problems += [f"context file in the working directory would be injected: {f}" for f in _context_files_near(Path(cwd))]
    return problems
