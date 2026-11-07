# Salt's Secrets

### Identity-based context isolation for Hermes Agent. One Hermes, two minds.

Your Hermes knows your passwords, your cat's name, your father's anniversary and the deploy key of
your server, because you told it. The moment you hand that same Hermes to other people, through a
Telegram bot, a Discord server or a web chat, every one of those facts is one question away.

This project gives Hermes a second mind. Strangers never reach your agent. They reach a **public profile**
that is built from nothing but what you ticked as public, rewritten in a guest-safe voice, and that
cannot reach your files, your sessions or your memory even if it wanted to. You keep one Hermes,
one gateway, one API key. Only the context is split, and it is split by **who is asking**.

| | Your session | The session strangers reach |
|---|---|---|
| SOUL.md | yours | its own, written for strangers |
| MEMORY.md / USER.md | injected every turn | switched off; only ticked lines arrive, as rewritten facts |
| Workspace, private/, skills | readable with read_file | no read_file, no path leads there |
| Sessions | session_search over everything | its own state.db, holding only guest conversations |
| Tools | every toolset | six toolsets: web, vision, image, speech, clarify, public lookup |
| Where it runs | `/v1` on the API server | `/p/public/v1`, same gateway, same process |

It is one Hermes plugin, `salts-secrets`, on top of an unmodified Hermes v0.23.0. No core file is patched.

## 30-second tour

The same eight questions, asked in parallel to both sessions (owner in the first person, guest about
"Salt", the owner's public persona). From the demo in [`demo/`](demo/) (a fictional persona):

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

Requires Hermes Agent **>= 0.23.0** with the API server enabled, and in-process plugins
(`plugins.isolation: in_process`, see [Limits](#limits)).

```bash
hermes plugins install salts-secrets          # into your own (owner) profile
hermes salts-secrets setup --owner-name "your handle"
hermes gateway restart
hermes salts-secrets check                    # ISOLATION OK · 10 checks
```

From a checkout instead of the catalog: `hermes plugins install dotv-7/salt-s-secrets/salts_secrets`.

`setup` is idempotent. It:

1. creates the public profile (`<HERMES_HOME>/profiles/public/`) with its own SOUL.md, config.yaml,
   API key and an empty working directory, so no `AGENTS.md` or `.hermes.md` can be picked up;
2. installs this same plugin there, where it acts as the guest side;
3. turns on `gateway.multiplex_profiles`, so `/p/public/v1` is served by the same gateway;
4. stores the public profile's key in your profile as `SALTS_PUBLIC_API_KEY` through Hermes secrets;
5. narrows the public toolsets, writes the public projection (empty until you publish) and runs the check.

Then open the **Salt's Secrets** tab in the Hermes dashboard and tick what strangers may know.

## How it works

The plugin looks at the profile it is loaded in and plays one of two parts.

**Owner profile** (`pre_gateway_dispatch`). Only your own DM reaches your agent. Every other inbound
message, including yours in a group, channel or thread, is withheld and answered by the public profile
through the local API server under its own session (`guest-<platform>-<chat>`). If the public profile
cannot answer, the message is dropped with a short notice; it never falls through to your agent.
Bots are ignored and each guest gets 20 messages an hour (`guest_rate_per_hour`).

**Public profile** (`pre_tool_call`, a system prompt section, one tool). A default-deny tool gate with a
hard ceiling: no terminal, files, code, memory, sessions, skills, browser, cron, delegation or messaging,
whatever the config says. `vision_analyze` only takes http(s) URLs. The prompt gets "Notes about
<owner_name>": the facts you approved and the titles of approved documents, whose bodies come from
`public_notes_lookup`. Both read one file, the projection in the public profile's plugin data, and never
open anything in your profile.

**Dashboard** (owner only, behind the dashboard login).

- *Context*: every source Hermes builds your prompt from (memory, user profile, SOUL.md, workspace,
  skills, sessions, cron, providers), private by default. Tick an item to publish it, optionally with
  a rewritten guest version. An approved item that you later edit drops out of the projection until you
  approve it again. Unticking revokes: guest sessions that may have seen the item are archived for you
  and deleted from the public store.
- *Permissions*: the toolsets guests may use, inside the ceiling.
- *Guests*: every guest conversation, live and archived, with probes (passwords, PINs, real names,
  addresses) flagged. Never injected into any prompt.
- *Check*: the same isolation check as `hermes salts-secrets check`.

Paths are shown relative to `<HERMES_HOME>`.

## Settings

Under `plugins.entries.salts-secrets.settings` in your profile's config.yaml:

| Key | Default | |
|---|---|---|
| `owners` | first entry of each `<PLATFORM>_ALLOWED_USERS` | your identities, `platform:user_id` |
| `public_profile` | `public` | the profile that answers everyone else |
| `owner_name` | `the owner` | how the public profile refers to you; use a handle, not your real name |
| `guest_rate_per_hour` | `20` | messages per guest per hour |
| `workspaces` | `<HERMES_HOME>/workspace` | folders the Context tab offers |

## Security and disclosure

- **Credentials.** The only secret the plugin reads is `SALTS_PUBLIC_API_KEY`, through
  `agent.secret_scope.get_secret`, so a multiplexed gateway never hands one profile another's key.
  `setup` creates it with `hermes config set`; the plugin never opens `.env` or `auth.json` itself.
  `check` compares the owner's copy with the public profile's `API_SERVER_KEY` through Hermes'
  profile secret scope.
- **Network.** The owner side calls only the local API server (`127.0.0.1:8642` by default).
  Nothing leaves the machine except what Hermes itself sends to your model provider.
- **Files written.** `<HERMES_HOME>/plugin-data/salts-secrets/` in both profiles (allowlist, projection,
  tool tiers, guest archive and digest, the public profile's empty cwd), and the public profile's
  config.yaml and SOUL.md during `setup`.
- **Processes.** Revoking context runs `hermes sessions delete` for the affected guest sessions.
- No self-update, no patching of Hermes, no code downloaded at runtime.

## Limits

- **Needs `plugins.isolation: in_process`.** Under the plugin host, `pre_gateway_dispatch` gets a
  placeholder gateway and cannot reply, so guest messages are dropped (safe, but nothing is answered),
  and `hermes salts-secrets` is not wired into the CLI.
- One public profile serves all guests. Guests are isolated from you and from each other's history, but
  share one persona and one projection.
- On Telegram and Discord a guest session is keyed by chat, so a returning guest sees their own earlier
  conversation.
- Isolation is enforced by profiles, configuration and plugins inside one process, not by a kernel
  boundary. Configuration can drift; that is what `check` is for.

## Layout

```
salts_secrets/            the plugin (catalog subdir)
  route.py                owner side: who reaches which profile
  gate.py                 public side: tool allowlist, fail-closed
  public_context.py       public side: prompt section and public_notes_lookup
  context.py              scan context sources, allowlist, projection
  guests.py               guest archive, digest, revocation
  tiers.py                toolset tiers, profile config, isolation check
  cli.py                  hermes salts-secrets setup | check | sync | guests
  dashboard/              the dashboard tab and its API
  profile/                SOUL.md and config.yaml templates for the public profile
  tests/                  pytest salts_secrets/tests
demo/                     the Salt demo behind https://527.dotv.io/ (not part of the plugin)
```

## Why

Hermes issue [#527](https://github.com/NousResearch/hermes-agent/issues/527) asks for multi-user
permission tiers. Most proposals tier the *tools*. This tiers the *context*: a guest is not refused the
answer, they talk to an agent that never had it. Salt, the demo's public persona, keeps her secrets
not by refusing to tell you, but because they were never in the room.

Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.23.0. Branch `main` of this
repository is that release, untouched; branch `dotv` is this project on top of it.
