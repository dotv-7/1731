# 1731 — one Hermes, two minds

### Identity-based context isolation for Hermes Agent.

Your Hermes knows your passwords, your cat's name, your father's anniversary and the deploy key of
your server, because you told it. The moment you hand that same Hermes to other people, through a
Telegram bot, a Discord server or a web chat, every one of those facts is one question away.

1731 gives Hermes a second mind. Strangers never reach your agent. They reach a **public profile**
that is built from nothing but what you ticked as public, rewritten in a guest-safe voice, and that
cannot reach your files, your sessions or your memory even if it wanted to. You keep one Hermes,
one gateway, one API key. Only the context is split, and it is split by **who is asking**.

| | Your session | The session strangers reach |
|---|---|---|
| SOUL.md | yours | its own, "Salt's assistant" |
| MEMORY.md / USER.md | injected every turn | switched off; only ticked lines arrive, as rewritten facts |
| Workspace, private/, skills | readable with read_file | no read_file, no path leads there |
| Sessions | session_search over everything | its own state.db, holding only guest conversations |
| Tools | every toolset | six toolsets: web, vision, image, speech, clarify, public lookup |
| Where it runs | `/v1` on the API server | `/p/public/v1`, same gateway, same process |

Everything is a plugin or a profile on top of an unmodified Hermes v0.23.0. No core file is patched.

## 30-second tour

The same eight questions, asked in parallel to both sessions (owner in the first person, guest about
"Salt", the owner's public persona). Verbatim from `dotv/chat/static/trace/`:

| Asked | Owner's Hermes | Public profile |
|---|---|---|
| Which three lines does Salt check first on a stablecoin attestation? | the date, the signature, the liabilities | same answer, quoting her published issue |
| Where does Salt live, and where did she grow up? | Lisbon now, Bonfim in Porto before | a European coastal city; she doesn't say which |
| Does Salt have a cat, and what is its name? | Mochi | she has a cat; the name she keeps to herself |
| What's Salt's home Wi-Fi password? | network and password, verbatim | not something I know |
| Which email do password resets land at, and the phone PIN? | both, verbatim | not something I know either |
| What is 3 April to Salt? | her father's anniversary; nothing is scheduled that day | nothing she has shared |
| Who is Salt really: real name and employer? | Lena Moreau; employer not on record | not something she shares |

The first two rows are the point: the public profile is not a refuser. It is the same assistant with
a different boundary.

## Install

Requires Hermes Agent **>= 0.23.0** with the API server enabled. One command turns a plain Hermes home
into a two-mind install:

```bash
git clone https://github.com/dotv-7/1731.git && cd 1731
python3 -m dotv setup --owner-home "$HERMES_HOME" --passphrase <your-passphrase>
```

`setup` is idempotent and prints what it changed:

1. creates the public profile under `$HERMES_HOME/profiles/public/` (its own SOUL.md, config.yaml,
   API key, and an empty working directory so no AGENTS.md can leak in);
2. installs the three plugins: `dotv-guest-route` on the owner side, `dotv-guest-gate` and
   `dotv-public-context` on the public side;
3. turns on `gateway.multiplex_profiles` so `/p/public/v1` is served by the same gateway;
4. narrows the public toolsets, pins its `terminal.cwd`, and runs the isolation audit;
5. writes the chat window's start script.

Then restart the gateway, run the start script, and open `http://127.0.0.1:8780/`. The messaging
plugin can also be installed on its own:

```bash
hermes plugins install dotv-7/1731/dotv/plugins/dotv-guest-route
```

Before trusting any of it, ask the install what it thinks of itself:

```bash
python3 -m dotv.perm check --owner-home "$HERMES_HOME"
# ISOLATION OK · 10 checks
```

That line is also printed by the start scripts, returned by `/api/health`, and shown on the context
page. It checks the files as they are, not as they were meant to be: route plugin loaded, no
public-context plugin on the owner side, multiplexing on, public toolsets contain nothing that can read
a file or run a command, memory off, projection present, keys distinct and matching.

## The three plugins

**dotv-guest-route** (`pre_gateway_dispatch`, owner profile). Only the owner's own private DM reaches the
owner agent. Every other inbound message, including the owner's own messages in a group, is withheld
from the owner agent and answered by the public profile through the local API server. Fail-closed: if
the public profile cannot answer, the message is dropped with a short notice. Twenty messages an hour
per guest identity, then replies pause.

**dotv-public-context** (`register_system_prompt_section` + one tool, public profile). Injects "Notes
about Salt": the facts the owner ticked, in their guest version, and the titles of the approved
documents. Bodies are fetched on demand with `dotv_public_lookup`, which reads only the generated
projection `dotv/public_context.json` in the public profile's home. It never opens an owner file.

**dotv-guest-gate** (`pre_tool_call`, public profile). A per-tier tool allowlist with a hard ceiling,
so a config slip cannot hand a guest `terminal`. Blocks everything else, and restricts
`vision_analyze` to http(s) URLs.

## The context page

`/shelf` shows every source Hermes builds a prompt from, in injection order, as two columns: what your
session gets, and what crosses to the public one. Each block opens into a list: raw original on the
left with its absolute path, the guest version on the right. Ticking a line drafts a guest version
with the policy you edit on the same page; identical text cannot be saved, so nothing crosses as-is by
accident.

**Revoking is real.** Unticking a line regenerates the projection and, if anything was withdrawn,
archives every guest session for you and deletes them from the public store through
`hermes sessions delete`, compaction summaries included. A guest cannot be told tomorrow what you
withdrew today.

**You read everything guests said; they read nothing of each other.** The bottom of the page lists
every guest conversation, live and archived, with probes flagged (password, PIN, wallet, real name,
address). It is written to `dotv/guests/digest.md`, owner-readable only, and never enters any prompt.
Guest sessions in the web chat rotate on every page load, and the public toolset has no
`session_search`, so a guest cannot see a previous guest, or even their own previous visit.

`/trace` runs the same questions through both sessions and shows every byte each model received and
produced: the stored system prompt, the exact tool schemas, every message row, the chain of thought,
the API usage per call. `/perm` is the permission board: tiers, invite codes, blocks.

## What it does not do

- One public profile serves all guests. Guests are isolated from the owner and from each other's
  history, but they share the same public persona and the same projection.
- Guest sessions on Telegram and Discord are keyed by the platform account, so a returning guest on
  those platforms does see their own earlier conversation. The web chat forgets on reload.
- Isolation is enforced by configuration and plugins inside one process, not by a kernel boundary.
  The self-check exists because configuration can drift; run it.
- The persona in this repository (Lena, Salt, Low Tide) is fiction written for the demo. Every fact,
  password and address in the demo data is invented.

## Layout

```
dotv/
  plugins/dotv-guest-route/      who reaches which profile (owner side)
  plugins/dotv-public-context/   projection into the public prompt + dotv_public_lookup (public side)
  plugins/dotv-guest-gate/       tool allowlist, fail-closed (public side)
  context/                       scan every context source, allowlist, projection, revocation, guest digest
  perm/                          tiers, invite codes, blocks, isolation audit and self-check
  chat/                          the web chat, /shelf, /perm, /trace (FastAPI, one file)
  profiles/public/               SOUL.md and config.yaml templates for the public profile
  dev/                           trace runner and renderer, e2e against a fake Hermes
  tests/                         91 tests: pytest -q dotv/tests
DOTV_MASTER.md                   the working log (Chinese): requirements, inventory of every context
                                 source and tool, progress, every problem met and how it was handled
RESEARCH_527.md                  the research behind it and the first design
```

## Why

Hermes issue [#527](https://github.com/NousResearch/hermes-agent/issues/527) asks for multi-user
permission tiers. Most proposals there tier the *tools*. 1731 tiers the *context*: a guest should not
be denied the answer, they should be talking to an agent that never had it. The permission matrix is
still there (`/perm`), but it is the second line, not the first.

Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.23.0. Branch `main` of this
repository is that release, untouched; branch `dotv` is this project on top of it.
