"""``python3 -m dotv.perm sync|audit|check --owner-home /opt/data``: apply the permission board to every tier
profile (tool lists, isolated cwd, gate plugin) or report isolation problems without changing anything."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotv.perm import core as perm


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m dotv.perm")
    ap.add_argument("command", choices=("sync", "audit", "check"))
    ap.add_argument("--owner-home", default="/opt/data")
    ap.add_argument("--profile", action="append", default=[], metavar="TIER=NAME",
                    help="tier profile names (default admin=dotv-admin user=dotv-user guest=public)")
    a = ap.parse_args(argv)
    owner = Path(a.owner_home)
    names = {"admin": "dotv-admin", "user": "dotv-user", "guest": "public"}
    names.update(dict(p.split("=", 1) for p in a.profile))
    homes = {t: owner / "profiles" / n for t, n in names.items()}
    if a.command == "check":
        res = perm.isolation_check(owner, homes["guest"])
        print(res["line"])
        for c in res["checks"]:
            print(f"  [{'ok' if c['ok'] else 'FAIL'}] {c['text']}")
        return 0 if res["ok"] else 1
    if a.command == "sync":
        for tier, toolsets in perm.sync(owner, homes).items():
            print(f"{tier}: {', '.join(toolsets)}")
    failed = False
    for tier, home in homes.items():
        if not home.is_dir():
            print(f"[skip] {tier}: {home} not found")
            continue
        problems = perm.audit(home)
        failed |= bool(problems)
        print(f"[{'FAIL' if problems else 'ok'}] {tier}: {home}")
        for p in problems:
            print(f"    - {p}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
