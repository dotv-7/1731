"""``hermes salts-secrets``: set up, check and publish the public side from the owner profile."""

from __future__ import annotations

import argparse
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from . import _yaml as yaml
from . import context, guests, tiers
from .home import NAME, PUBLIC_KEY_ENV, current, data_dir

PLUGIN_DIR = Path(__file__).resolve().parent
TEMPLATES = PLUGIN_DIR / "profile"


def _read_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _hermes(*args: str, profile: str | None = None) -> None:
    """Run the Hermes CLI, so credentials and profiles are written by the code that owns them."""
    cmd = [sys.executable, "-m", "hermes_cli.main"] + (["-p", profile] if profile else []) + list(args)
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def _settings(cfg: dict) -> dict:
    entry = cfg.setdefault("plugins", {}).setdefault("entries", {}).setdefault(NAME, {})
    return entry.setdefault("settings", {})


def _enable(cfg: dict) -> None:
    enabled = cfg.setdefault("plugins", {}).setdefault("enabled", [])
    if NAME not in enabled:
        enabled.append(NAME)


def _paths(args: argparse.Namespace) -> tuple[Path, Path, dict]:
    owner = current()
    cfg = _read_yaml(owner / "config.yaml")
    public = args.public or _settings(cfg).get("public_profile") or "public"
    return owner, owner / "profiles" / public, cfg


def _workspaces(owner: Path, cfg: dict) -> list[Path]:
    return [Path(p).expanduser() for p in _settings(cfg).get("workspaces") or [owner / "workspace"]]


def cmd_setup(args: argparse.Namespace) -> int:
    owner, pub, owner_cfg = _paths(args)
    say = lambda text: print("  " + text)  # noqa: E731
    owner_name = args.owner_name or _settings(owner_cfg).get("owner_name") or "the owner"

    print(f"public profile: profiles/{pub.name}")
    if not pub.is_dir():
        _hermes("profile", "create", pub.name, "--no-skills", "--no-alias")
        say("created")
    soul = pub / "SOUL.md"
    if not soul.exists() or not soul.read_text(encoding="utf-8").strip():
        soul.write_text(
            (TEMPLATES / "SOUL.md").read_text(encoding="utf-8").format(owner_name=owner_name), encoding="utf-8"
        )
        say("SOUL.md written from the template; edit it to shape the public persona")

    cfg = _read_yaml(pub / "config.yaml")
    for key, value in _read_yaml(TEMPLATES / "config.yaml").items():
        cfg[key] = value if not isinstance(value, dict) else {**(cfg.get(key) or {}), **value}
    if owner_cfg.get("model") and "model" not in cfg:
        cfg["model"] = owner_cfg["model"]
        say("model settings copied from the owner profile")
    _enable(cfg)
    _settings(cfg).update(public_profile=pub.name, owner_name=owner_name)
    _write_yaml(pub / "config.yaml", cfg)

    target = pub / "plugins" / NAME
    if target.resolve() != PLUGIN_DIR:
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(PLUGIN_DIR, target, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    say(f"{NAME} installed and enabled")

    print("owner profile")
    _enable(owner_cfg)
    owner_cfg.setdefault("gateway", {})["multiplex_profiles"] = True
    _settings(owner_cfg).update(public_profile=pub.name, owner_name=owner_name)
    _write_yaml(owner / "config.yaml", owner_cfg)
    say("gateway.multiplex_profiles on, plugin enabled")

    public_key = tiers.profile_secrets(pub).get("API_SERVER_KEY") or f"salts-public-{secrets.token_hex(16)}"
    _hermes("config", "set", "API_SERVER_KEY", public_key, profile=pub.name)
    _hermes("config", "set", PUBLIC_KEY_ENV, public_key)
    if not tiers.profile_secrets(owner).get("API_SERVER_KEY"):
        _hermes("config", "set", "API_SERVER_KEY", f"salts-owner-{secrets.token_hex(16)}")
    say(f"public API key stored in both profiles ({PUBLIC_KEY_ENV})")

    print("tiers and projection")
    _sync(owner, pub, owner_cfg, say)
    print()
    return cmd_check(args) or _next_steps(pub.name)


def _next_steps(public: str) -> int:
    print(
        "\nnext:\n  1. restart the gateway (hermes gateway restart)\n"
        f"  2. set your own ids: hermes config set plugins.entries.{NAME}.settings.owners '[\"telegram:<id>\"]'\n"
        "  3. open the dashboard (hermes dashboard) and approve what guests may know under Context"
    )
    return 0


def _sync(owner: Path, pub: Path, owner_cfg: dict, say) -> None:
    for tier, toolsets in tiers.sync(owner, {"guest": pub}).items():
        say(f"{tier}: {', '.join(toolsets)}")
    projection = context.sync_public(
        owner, pub, _workspaces(owner, owner_cfg), _settings(owner_cfg).get("owner_name") or "the owner"
    )
    say(f"published {len(projection['facts'])} facts and {len(projection['documents'])} documents")


def cmd_sync(args: argparse.Namespace) -> int:
    owner, pub, owner_cfg = _paths(args)
    _sync(owner, pub, owner_cfg, lambda text: print("  " + text))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    owner, pub, _ = _paths(args)
    result = tiers.isolation_check(owner, pub)
    for check in result["checks"]:
        print(f"  {'ok  ' if check['ok'] else 'FAIL'} {check['text']}")
    print(result["line"])
    return 0 if result["ok"] else 1


def cmd_guests(args: argparse.Namespace) -> int:
    owner, pub, _ = _paths(args)
    counts = guests.digest(owner, pub)["counts"]
    print(f"{counts['sessions']} guest sessions, {counts['questions']} questions, {counts['probes']} probes")
    print(f"digest: {data_dir(owner) / guests.DIGEST_FILE}")
    return 0


def setup_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--public", help="public profile name (default: public)")
    sub = parser.add_subparsers(dest="salts_command", required=True)
    setup = sub.add_parser("setup", help="create the public profile and install the plugin on both sides")
    setup.add_argument("--owner-name", help="how the public profile refers to you (a handle, not your real name)")
    setup.set_defaults(salts_handler=cmd_setup)
    sub.add_parser("check", help="verify every isolation condition").set_defaults(salts_handler=cmd_check)
    sub.add_parser("sync", help="publish approved context and tool tiers").set_defaults(salts_handler=cmd_sync)
    sub.add_parser("guests", help="summarise guest conversations").set_defaults(salts_handler=cmd_guests)


def handle(args: argparse.Namespace) -> int:
    return args.salts_handler(args)


def register(ctx) -> None:
    ctx.register_cli_command(
        name=NAME, help="Isolate guests from your private context", setup_fn=setup_parser, handler_fn=handle
    )
