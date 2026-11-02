# dotv add-ons for Hermes (pluggable)

Everything here installs on top of an official Hermes image (v0.22.2+); no Hermes core file is changed.
See `DOTV_MASTER.md` for requirements, deliverables, tool/context inventory and the role matrix.

## Step 1: public (Guest) profile + tool gate

| Path | What |
|---|---|
| `profiles/public/config.yaml` | Guest profile settings: only `web`, `vision`, `image_gen`, `tts`, `clarify` toolsets on the API server; no external memory provider; no memory review; private URLs blocked; sessions kept; `terminal.cwd` pinned to an empty dir |
| `profiles/public/SOUL.md` | Guest persona with the "this is a Guest session" notice (fallback only) |
| `plugins/dotv-guest-gate/` | `pre_tool_call` plugin: blocks every tool outside the allowlist (fail-closed); `vision_analyze` only accepts http(s) URLs |

Install (inside the `hermes` container, `HERMES_HOME=/opt/data`):

```bash
hermes profile create public
cp dotv/profiles/public/SOUL.md /opt/data/profiles/public/SOUL.md
# merge dotv/profiles/public/config.yaml into /opt/data/profiles/public/config.yaml
cp -r dotv/plugins/dotv-guest-gate /opt/data/profiles/public/plugins/
# give the public profile its own API_SERVER_KEY in /opt/data/profiles/public/.env
python3 -m dotv.perm sync --owner-home /opt/data    # pins terminal.cwd, enables the gate, then audits
```

Why `terminal.cwd` matters: a profile without it runs in the home directory (or `MESSAGING_CWD`), and
any `AGENTS.md` / `.hermes.md` / `CLAUDE.md` found there (or up the git root) is injected into the
Guest's system prompt. `sync` pins every non-owner profile to its own empty `<profile>/dotv/cwd`;
`python3 -m dotv.perm audit --owner-home /opt/data` only checks (exit 1 on any problem: wrong cwd,
context files near it, dangerous toolsets, `tool_search` on, memory provider, private URLs, gate missing).
The same check is shown on `/perm`.

Requests to `/p/public/v1/chat/completions` with the public key then run as Guest. Plugins load per
HERMES_HOME, so the gate never affects the owner's `default` profile.

Tests (need FastAPI/httpx for `test_chat_server.py`; plain system Python fails with `No module named 'fastapi'`):

```bash
# Hermes test environment (CONTRIBUTING.md), which already has fastapi/httpx/uvicorn:
python -m pm.build_env --source . --out .venv --group dev --group test   # once, after `source ./activate`
scripts/run_tests.sh dotv/tests
# or standalone, without Hermes:
python3 -m venv ~/dotv-venv && ~/dotv-venv/bin/pip install -r dotv/requirements-dev.txt
~/dotv-venv/bin/python -m pytest -q dotv/tests
```

YAML goes through `dotv/_yaml.py` (PyYAML if present, else Hermes' `hermes_yaml`), so the dotv code also
runs with the Hermes interpreter inside the official image, which has no PyYAML.

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

## Step 4: permission tiers (#527) + permission board

`dotv/perm/` + `/perm` (owner only). Tiers: Owner (passphrase, default profile, everything) → Admin →
User → Guest (no login) → Blocked (never reaches Hermes). Admin/User/Guest are each their own profile
(`DOTV_ADMIN_PROFILE`/`DOTV_USER_PROFILE`, default `dotv-admin`/`dotv-user`; Guest = `public`), set up
like the public profile, each with its own `API_SERVER_KEY` passed as `DOTV_ADMIN_KEY`/`DOTV_USER_KEY`.

- Policy: `<owner_home>/dotv/permissions.json` (toolsets per tier + members; invite codes stored only
  as salted hashes). Toolsets that reach the owner's machine (terminal, files, code, browser, cron,
  delegation, messaging…) are locked for every non-owner tier and cannot be saved.
- "保存并同步" writes `<profile>/dotv/tier.json` (read by `dotv-guest-gate`, capped by its `CEILING`)
  and narrows `platform_toolsets.api_server` + `memory.memory_enabled` in the profile's `config.yaml`.
  New sessions pick it up; restart the gateway after creating a new tier profile.
- Members get an invite code (`/unlock <code>` in the chat). Tier changes, blocking and deletion take
  effect on the next request.
- Blocking survives a cleared cookie: every member gets a signed device id, and the last few device ids
  and public IPs they used are remembered. Setting a member to Blocked puts those into the blocklist
  (entries expire after 90 days); a blocked request returns 403 instead of
  falling back to Guest. Owner passphrase always wins (same NAT). Deleting a member does not lift the
  block; unblock entries on `/perm`. Loopback/LAN/container addresses are never IP-blocked: behind
  nginx on the same host uvicorn already takes the client IP from `X-Forwarded-For` (`proxy_headers`,
  trusted `127.0.0.1`); a proxy elsewhere needs `FORWARDED_ALLOW_IPS=<proxy ip>`.
- `/perm` can turn off anonymous Guest chat entirely (only invite codes and the owner can chat).
- `/docs` (owner only) renders `DOTV_DOC_PATH` (default: repo `DOTV_MASTER.md`).

Local preview without a model: `dotv/dev/seed_demo.py` (fake data) + `dotv/dev/fake_hermes.py`.

## Real-Hermes end-to-end check (`dotv/dev/e2e/`)

Runs this checkout's real gateway with an owner home plus a `public` profile, against a scripted
OpenAI-compatible model that reports what Hermes put in its context and requests forbidden tools.

```bash
python3 dotv/dev/e2e/fake_llm.py &                 # :8700
bash dotv/dev/e2e/setup.sh                          # wipes and creates $E2E_ROOT (default ~/hermes-e2e/root)
HERMES_HOME=~/hermes-e2e/root HERMES_RUNTIME_DIR=~/hermes-e2e/run bash -c 'source ./activate && hermes gateway run' &
python3 dotv/dev/e2e/probe.py                       # guest: no owner canary, forbidden tools "do not exist"
```

`setup.sh` also plants an `AGENTS.md` canary in the owner's workspace and runs `python3 -m dotv.perm sync`.
To see the cwd leak it prevents: `E2E_NO_SYNC=1 bash dotv/dev/e2e/setup.sh`, put an `AGENTS.md`
containing `OWNER_CONTEXT_CANARY` in the gateway user's home, and the probe reports
`owner_context_file_in_context: true` for the guest.
