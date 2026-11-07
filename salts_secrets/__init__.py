"""Salt's Secrets: one Hermes, two minds.

The same plugin is installed in two profiles and plays a different part in each:

- owner profile: routes every message that is not the owner's own DM to the public profile, adds the
  ``hermes salts-secrets`` command and the Context / Permissions / Trace dashboard tabs;
- public profile: refuses every tool outside the guest allowlist and injects only what the owner
  approved for strangers.
"""

from __future__ import annotations

import logging

from . import home

log = logging.getLogger(__name__)


def register(ctx) -> None:
    public_profile = ctx.get_config("public_profile", default="public") or "public"
    if home.is_public(home.current(), public_profile):
        from . import gate, public_context

        gate.register(ctx)
        public_context.register(ctx)
        log.info("salts-secrets: public role")
        return
    from . import cli, route

    route.register(ctx)
    cli.register(ctx)
    log.info("salts-secrets: owner role")
