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

## Step 2: context dashboard + public allowlist + sync

| Path | What |
|---|---|
| `context/core.py` | Scans everything that can reach the owner's context (MEMORY.md / USER.md entries, SOUL.md, workspace text files split by heading, skills, history sessions, external memory provider, plugins); allowlist in `$OWNER_HOME/dotv/public_allowlist.json` (original files untouched); one-way sync to `$PUBLIC_HOME/dotv/public_context.json` |
| `plugins/dotv-public-context/` | Public-profile plugin: approved memory facts go into the system prompt; approved documents via `dotv_public_lookup` |

Private by default. An approved item whose content changes is treated as private again ("changed") until re-approved.
History sessions, external memory and plugin context are listed but cannot be made public.

## Step 3: dotv-chat (independent window)

`chat/server.py` (FastAPI) serves the terminal-style chat at `/` and the owner-only dashboard at `/board`.
The browser never gets a Hermes key; guests go to `/p/public/v1`, the owner (after `/unlock <passphrase>`) to `/v1`.

```bash
python3 dotv/chat/server.py hash-passphrase   # -> DOTV_OWNER_PASSPHRASE_HASH
DOTV_HERMES_URL=http://127.0.0.1:8642 DOTV_OWNER_KEY=... DOTV_PUBLIC_KEY=... \
DOTV_OWNER_PASSPHRASE_HASH=... DOTV_SECRET=<random> DOTV_OWNER_HOME=/opt/data \
DOTV_WORKSPACE_ROOTS=/opt/data/workspace python3 dotv/chat/server.py   # :8780
```

Local preview without a model: `dotv/dev/seed_demo.py` (fake data) + `dotv/dev/fake_hermes.py`.
