"""`python3 -m dotv setup`: one command from a plain Hermes home to a working dotv install.

What it does, in order (each step is idempotent and says what it changed):
  1. public profile dir under <owner-home>/profiles/<public>/: SOUL.md template, config.yaml (template merged with
     the owner's model block), .env with its own API_SERVER_KEY, dotv/cwd (the empty working directory)
  2. plugins: dotv-public-context and dotv-guest-gate into the public profile, dotv-guest-route into the owner profile
  3. gateway.multiplex_profiles: true in the owner config (so /p/<public>/v1 is served by the same gateway)
  4. permission tiers: python -m dotv.perm sync (narrow toolsets, tier.json, terminal.cwd) + isolation audit
  5. chat window: passphrase hash, random secret, a start script at <owner-home>/dotv/start_chat.sh
Then it prints what to restart and where the pages are.
"""
import argparse, secrets, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _yaml():
    from dotv import _yaml
    return _yaml


def _read_yaml(p: Path) -> dict:
    y = _yaml()
    try:
        return y.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, y.YAMLError):
        return {}


def _write_yaml(p: Path, data: dict) -> None:
    p.write_text(_yaml().safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _env_get(p: Path, key: str) -> str:
    if not p.exists():
        return ""
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _env_set(p: Path, key: str, value: str) -> None:
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    lines = [l for l in lines if not l.startswith(key + "=")] + [f"{key}={value}"]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    p.chmod(0o600)


def cmd_setup(a: argparse.Namespace) -> int:
    owner = Path(a.owner_home).expanduser().resolve()
    if not (owner / "config.yaml").exists():
        print(f"no config.yaml in {owner}: point --owner-home at your HERMES_HOME", file=sys.stderr)
        return 2
    pub = owner / "profiles" / a.public
    say = lambda s: print("  " + s)
    print(f"owner home   {owner}\npublic home  {pub}")

    # 1. public profile
    print("1. public profile")
    pub.mkdir(parents=True, exist_ok=True)
    (pub / "dotv" / "cwd").mkdir(parents=True, exist_ok=True)
    soul = pub / "SOUL.md"
    if not soul.exists():
        shutil.copy(HERE / "profiles" / "public" / "SOUL.md", soul); say("SOUL.md: template installed (edit it: this is the public persona)")
    else:
        say("SOUL.md: kept")
    owner_cfg = _read_yaml(owner / "config.yaml")
    tmpl = _read_yaml(HERE / "profiles" / "public" / "config.yaml")
    cfg = _read_yaml(pub / "config.yaml")
    for k, v in tmpl.items():
        if k not in cfg:
            cfg[k] = v
    if "model" not in cfg and owner_cfg.get("model"):
        cfg["model"] = owner_cfg["model"]; say("config.yaml: model block copied from the owner config")
    cfg.setdefault("terminal", {})["cwd"] = str(pub / "dotv" / "cwd")
    _write_yaml(pub / "config.yaml", cfg); say("config.yaml: written (memory off, 6 toolsets, reasoning off, empty cwd)")
    pub_env = pub / ".env"
    pub_key = _env_get(pub_env, "API_SERVER_KEY")
    if not pub_key:
        pub_key = "dotv-public-" + secrets.token_hex(12); _env_set(pub_env, "API_SERVER_KEY", pub_key); say(".env: new API_SERVER_KEY for the public profile")
    else:
        say(".env: API_SERVER_KEY kept")

    # 2. plugins
    print("2. plugins")
    for name, home in (("dotv-public-context", pub), ("dotv-guest-gate", pub), ("dotv-guest-route", owner)):
        dst = home / "plugins" / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(HERE / "plugins" / name, dst); say(f"{name} -> {dst}")
    cfg = _read_yaml(pub / "config.yaml")
    en = cfg.setdefault("plugins", {}).setdefault("enabled", [])
    for name in ("dotv-public-context", "dotv-guest-gate"):
        if name not in en:
            en.append(name)
    _write_yaml(pub / "config.yaml", cfg)
    ocfg = _read_yaml(owner / "config.yaml")
    oen = ocfg.setdefault("plugins", {}).setdefault("enabled", [])
    if "dotv-guest-route" not in oen:
        oen.append("dotv-guest-route")

    # 3. multiplex
    print("3. gateway")
    ocfg.setdefault("gateway", {})["multiplex_profiles"] = True
    _write_yaml(owner / "config.yaml", ocfg); say("owner config.yaml: gateway.multiplex_profiles=true, dotv-guest-route enabled")
    owner_key = _env_get(owner / ".env", "API_SERVER_KEY")
    if not owner_key:
        owner_key = "dotv-owner-" + secrets.token_hex(12)
        _env_set(owner / ".env", "API_SERVER_KEY", owner_key); say(".env: new API_SERVER_KEY for the owner profile")
    if not _env_get(owner / ".env", "API_SERVER_ENABLED"):
        _env_set(owner / ".env", "API_SERVER_ENABLED", "true")
    _env_set(owner / ".env", "DOTV_PUBLIC_KEY", pub_key)

    # 4. permissions
    print("4. permission tiers")
    from dotv.perm import core as perm
    homes = {"admin": owner / "profiles" / "dotv-admin", "user": owner / "profiles" / "dotv-user", "guest": pub}
    for tier, toolsets in perm.sync(owner, homes).items():
        say(f"{tier}: {', '.join(toolsets)}")
    problems = perm.audit(pub)
    say("isolation audit: " + ("passed" if not problems else "FAILED"))
    for p in problems:
        say("   - " + p)

    # 5. chat window
    print("5. chat window")
    from dotv.chat.server import hash_passphrase
    passphrase = a.passphrase or secrets.token_hex(3)
    env = {
        "PYTHONPATH": str(HERE.parent), "DOTV_OWNER_HOME": str(owner), "DOTV_PUBLIC_HOME": str(pub),
        "DOTV_PUBLIC_PROFILE": a.public, "DOTV_WORKSPACE_ROOTS": str(owner / "workspace"),
        "DOTV_HERMES_URL": a.hermes, "DOTV_OWNER_KEY": owner_key, "DOTV_PUBLIC_KEY": pub_key,
        "DOTV_SECRET": secrets.token_hex(32), "DOTV_OWNER_PASSPHRASE_HASH": hash_passphrase(passphrase),
        "DOTV_HOST": "127.0.0.1", "DOTV_PORT": str(a.port),
    }
    script = owner / "dotv" / "start_chat.sh"
    script.parent.mkdir(exist_ok=True)
    lines = ["#!/bin/sh", "# generated by python3 -m dotv setup; re-run setup to regenerate", 'pkill -f "dotv/chat/server.py" 2>/dev/null; sleep 1']
    lines += [f"export {k}='{v}'" for k, v in env.items()]
    lines += [f"cd '{HERE.parent}' && ( nohup '{sys.executable}' dotv/chat/server.py > '{owner / 'dotv' / 'chat.log'}' 2>&1 < /dev/null & )",
              f"sleep 2; curl -fsS -o /dev/null http://127.0.0.1:{a.port}/api/health && echo 'dotv chat: http://127.0.0.1:{a.port}/  (owner passphrase in {owner / 'dotv' / 'passphrase.txt'})'"]
    script.write_text("\n".join(lines) + "\n", encoding="utf-8"); script.chmod(0o700)
    (owner / "dotv" / "passphrase.txt").write_text(passphrase + "\n", encoding="utf-8"); (owner / "dotv" / "passphrase.txt").chmod(0o600)
    say(f"start script: {script}")
    say(f"owner passphrase: {passphrase}  (saved in dotv/passphrase.txt, 600)")

    print("\nnext:")
    print("  1. restart the Hermes gateway so it loads the public profile, the plugins and multiplexing")
    print(f"  2. sh {script}")
    print(f"  3. open http://127.0.0.1:{a.port}/ and unlock with the passphrase; CONTEXT is at /shelf, PERMISSIONS at /perm")
    print("  4. messaging: set dotv-guest-route's owner ids in the owner config (plugins.dotv-guest-route.owners) so only your own DM reaches your profile")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m dotv")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="install everything into a Hermes home")
    s.add_argument("--owner-home", default=str(Path.home() / ".hermes"))
    s.add_argument("--public", default="public", help="public profile name")
    s.add_argument("--passphrase", help="owner passphrase for the chat window (random if omitted)")
    s.add_argument("--port", type=int, default=8780)
    s.add_argument("--hermes", default="http://127.0.0.1:8642")
    a = ap.parse_args(argv)
    if a.cmd == "setup":
        return cmd_setup(a)
    return 1


if __name__ == "__main__":
    sys.exit(main())
