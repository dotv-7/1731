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
import json
import os
import secrets
import time
from pathlib import Path
from typing import Iterable, Optional

POLICY_FILE = "dotv/permissions.json"
TIER_FILE = "dotv/tier.json"
TIERS = ("owner", "admin", "user", "guest", "blocked")
CONFIGURABLE = ("admin", "user", "guest")
TIER_LABEL = {"owner": "Owner", "admin": "Admin", "user": "User", "guest": "访客", "blocked": "已封禁"}

# toolset -> (tools, category, label, lock reason for non-owner tiers or "", shared-state warning or "")
CATALOG: dict[str, tuple[tuple[str, ...], str, str, str, str]] = {
    "web": (("web_search", "web_extract"), "网页", "搜索 + 抓取网页", "", ""),
    "x_search": (("x_search",), "网页", "X/Twitter 搜索", "", ""),
    "vision": (("vision_analyze",), "看图看视频", "看图（仅网址）", "", ""),
    "video": (("video_analyze",), "看图看视频", "看视频", "", ""),
    "image_gen": (("image_generate",), "生成", "生成图片", "", "耗你的 API 额度"),
    "video_gen": (("video_generate", "xai_video_edit", "xai_video_extend"), "生成", "生成视频", "", "耗你的 API 额度"),
    "tts": (("text_to_speech",), "生成", "语音朗读", "", ""),
    "clarify": (("clarify",), "对话", "追问澄清", "", ""),
    "dotv_public": (("dotv_public_lookup",), "对话", "查公开资料", "", ""),
    "todo": (("todo_list",), "对话", "待办清单", "", ""),
    "memory": (("memory",), "记忆", "记忆（该级别自己的）", "", "同级别成员共享一份"),
    "session_search": (("session_search",), "记忆", "搜索历史会话（该级别的）", "", "同级别成员能搜到彼此的会话"),
    "skills": (("skills_list", "skill_view", "skill_manage"), "技能", "技能（该级别自己的）", "", "同级别成员共享"),
    "terminal": (("terminal", "process_manage"), "终端代码", "终端", "能读到你机器上的任何文件", ""),
    "code_execution": (("execute_code",), "终端代码", "执行代码", "能读到你机器上的任何文件", ""),
    "file": (("read_file", "write_file", "patch", "search_files"), "文件", "读写文件", "能读到你的私密文件", ""),
    "browser": (("browser_navigate",), "浏览器", "浏览器（14 个工具）", "能用你已登录的网站、打开本地文件", ""),
    "computer_use": (("computer_use",), "浏览器", "电脑操作", "能操作你的桌面", ""),
    "cronjob": (("cronjob_manage",), "调度协作", "定时任务", "以你的身份长期运行", ""),
    "delegation": (("delegate_task",), "调度协作", "派子任务", "子任务不受闸门约束", ""),
    "kanban": (("kanban_create",), "调度协作", "Kanban 任务", "能看到你的任务", ""),
    "connections": (("manage_connections",), "对外", "账号连接", "能用你的账号", ""),
    "messaging": (("send_message",), "对外", "对外发消息", "以你的名义发消息", ""),
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


def load(owner_home: Path) -> dict:
    try:
        data = json.loads((Path(owner_home) / POLICY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    tiers = data.get("tiers") if isinstance(data.get("tiers"), dict) else {}
    return {"tiers": {t: _clean(tiers.get(t, DEFAULT_TIERS[t])) for t in CONFIGURABLE},
            "members": [m for m in data.get("members") or [] if isinstance(m, dict) and m.get("tier") in TIERS[1:]]}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def save(owner_home: Path, policy: dict) -> dict:
    clean = {"tiers": {t: _clean((policy.get("tiers") or {}).get(t, [])) for t in CONFIGURABLE},
             "members": policy.get("members") or []}
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
    member = {"id": secrets.token_hex(6), "name": name.strip()[:40] or "未命名", "tier": tier,
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
    save(owner_home, policy)
    return True


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


def _narrow_config(path: Path, toolsets: list[str]) -> None:
    if not path.exists():
        return
    import yaml
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg.setdefault("platform_toolsets", {})["api_server"] = list(toolsets)
    mem = cfg.setdefault("memory", {})
    mem["memory_enabled"] = "memory" in toolsets
    cfg.setdefault("tools", {}).setdefault("tool_search", {})["enabled"] = "off"
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)
