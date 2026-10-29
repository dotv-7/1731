# dotv add-ons for Hermes (pluggable)

Everything here installs on top of an official Hermes image (v0.22.2+); no Hermes core file is changed.
See `DOTV_MASTER.md` for requirements, deliverables, tool/context inventory and the role matrix.

## Step 1: public (Guest) profile + tool gate

| Path | What |
|---|---|
| `profiles/public/config.yaml` | Guest profile settings: only `web`, `vision`, `image_gen`, `tts`, `clarify` toolsets on the API server; no external memory provider; no memory review; private URLs blocked; sessions kept |
| `profiles/public/SOUL.md` | Guest persona with the "this is a Guest session" notice (fallback only) |
| `plugins/dotv-guest-gate/` | `pre_tool_call` plugin: blocks every tool outside the allowlist (fail-closed); `vision_analyze` only accepts http(s) URLs |

Install (inside the `hermes` container, `HERMES_HOME=/opt/data`):

```bash
hermes profile create public
cp dotv/profiles/public/SOUL.md /opt/data/profiles/public/SOUL.md
# merge dotv/profiles/public/config.yaml into /opt/data/profiles/public/config.yaml
cp -r dotv/plugins/dotv-guest-gate /opt/data/profiles/public/plugins/
# give the public profile its own API_SERVER_KEY in /opt/data/profiles/public/.env
```

Requests to `/p/public/v1/chat/completions` with the public key then run as Guest. Plugins load per
HERMES_HOME, so the gate never affects the owner's `default` profile.

Tests: `python3 -m pytest dotv/tests` (pure Python; needs `pytest` and `pyyaml`).
