"""Owner-only dashboard API, mounted by Hermes at /api/plugins/salts-secrets/ behind the dashboard auth gate.

The dashboard runs in the owner profile, so the owner home is the current Hermes home and the public
profile is the one named in this plugin's settings.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


def _load_package():
    """Import the plugin directory as a package whatever its install directory is called."""
    root = Path(__file__).resolve().parents[1]
    name = f"_{root.name.replace('-', '_')}_dashboard"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
    return [importlib.import_module(f"{name}.{m}") for m in ("_yaml", "context", "guests", "tiers", "home")]


yaml, context, guests, tiers, home = _load_package()
NAME, data_dir = home.NAME, home.data_dir


def current() -> Path:
    return home.current()


router = APIRouter()


def _settings() -> dict:
    try:
        cfg = yaml.safe_load((current() / "config.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    entry = ((cfg.get("plugins") or {}).get("entries") or {}).get(NAME) or {}
    return entry.get("settings") or {}


def _homes() -> tuple[Path, Path, list[Path], str]:
    owner, settings = current(), _settings()
    public = owner / "profiles" / str(settings.get("public_profile") or "public")
    workspaces = [Path(p).expanduser() for p in settings.get("workspaces") or [owner / "workspace"]]
    return owner, public, workspaces, str(settings.get("owner_name") or "the owner")


def _projection(public: Path) -> dict:
    try:
        return json.loads((data_dir(public) / context.PROJECTION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"facts": [], "documents": []}


@router.get("/items")
def items():
    owner, _, workspaces, owner_name = _homes()
    allowlist = context.load_allowlist(owner)
    return {
        "owner_name": owner_name,
        "items": [
            dict(
                item.to_dict(with_text=False),
                status=context.status(item, allowlist),
                public_text=allowlist.get(item.id, {}).get("public_text", ""),
            )
            for item in context.scan(owner, workspaces)
        ],
    }


@router.get("/items/{item_id}")
def item_text(item_id: str):
    owner, _, workspaces, _ = _homes()
    item = next((i for i in context.scan(owner, workspaces) if i.id == item_id), None)
    if item is None:
        raise HTTPException(404, "item changed or was removed; reload")
    return {"text": item.text}


class Publish(BaseModel):
    public_ids: list[str] = []
    public_texts: dict[str, str] = {}


@router.post("/publish")
def publish(body: Publish):
    """Save the allowlist, rewrite the public projection, and purge guest sessions that saw revoked items."""
    owner, public, workspaces, owner_name = _homes()
    context.save_allowlist(owner, context.scan(owner, workspaces), body.public_ids, body.public_texts)
    before = _projection(public)
    after = context.sync_public(owner, public, workspaces, owner_name)
    purge = guests.revoke_and_purge(owner, public, before, after)
    return {"facts": len(after["facts"]), "documents": len(after["documents"]), **purge}


@router.get("/preview")
def preview():
    return _projection(_homes()[1])


@router.get("/guests")
def guest_sessions():
    owner, public, _, _ = _homes()
    return guests.digest(owner, public, write=False)


@router.post("/guests/clear")
def clear_guests():
    owner, public, _, _ = _homes()
    archived = guests.archive(owner, public, reason="owner cleared")
    return {"archived": archived, **guests.purge(public)}


@router.get("/permissions")
def permissions():
    owner = _homes()[0]
    catalog = [
        {
            "id": key,
            "tools": list(tools),
            "category": category,
            "label": label,
            "locked": bool(locked),
            "locked_reason": locked,
            "warning": warning,
        }
        for key, (tools, category, label, locked, warning) in tiers.CATALOG.items()
    ]
    return {"catalog": catalog, "guest": tiers.load(owner)["tiers"]["guest"]}


class Toolsets(BaseModel):
    guest: list[str]


@router.post("/permissions")
def save_permissions(body: Toolsets):
    owner, public, _, _ = _homes()
    policy = tiers.load(owner)
    policy["tiers"]["guest"] = body.guest
    tiers.save(owner, policy)
    return {"guest": tiers.sync(owner, {"guest": public}).get("guest", [])}


@router.get("/check")
def check():
    owner, public, _, _ = _homes()
    return tiers.isolation_check(owner, public)
