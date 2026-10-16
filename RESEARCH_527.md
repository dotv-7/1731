# Research: upstream #527 — gateway permission tiers (Owner / Admin / User / Guest)

Scope: research and a Phase 1 design only. No runtime code is changed by this document.
Snapshot: `NousResearch/hermes-agent@74e7a25c9496`. All file/line references below are against this tree.

Sources read: issue [#527](https://github.com/NousResearch/hermes-agent/issues/527) (body + all comments),
related issues [#110686](https://github.com/NousResearch/hermes-agent/issues/110686),
[#11430](https://github.com/NousResearch/hermes-agent/issues/11430),
[#112119](https://github.com/NousResearch/hermes-agent/issues/112119),
[#62549](https://github.com/NousResearch/hermes-agent/issues/62549),
[#28279](https://github.com/NousResearch/hermes-agent/issues/28279), and PRs
[#3156](https://github.com/NousResearch/hermes-agent/pull/3156),
[#4443](https://github.com/NousResearch/hermes-agent/pull/4443),
[#22509](https://github.com/NousResearch/hermes-agent/pull/22509),
[#58282](https://github.com/NousResearch/hermes-agent/pull/58282),
[#137975](https://github.com/NousResearch/hermes-agent/pull/137975) (and the merged
[#23373](https://github.com/NousResearch/hermes-agent/pull/23373) that superseded #4443).

---

## 1. What the maintainer wants

### 1.1 Issue body (teknium1, 2026-03-06)

- Ordered tiers `OWNER > ADMIN > USER > GUEST > BLOCKED`, configured under a `permissions:` block in
  `config.yaml` (`tiers`, `users: {"telegram:123": owner, ...}`, `default_tier`, `pairing_tier`).
- Enforcement is **deterministic and server-side**: tools are filtered *before* `AIAgent()` is constructed,
  not by asking the model to behave.
- Phase 1 in the issue: a permission manager, config schema, tool filtering at agent build, command gating,
  and the caller's tier surfaced in the session-context prompt.
- **Backward compatible**: no `permissions` config ⇒ behaviour unchanged (every authorised user is
  effectively owner).
- Guest was originally described as "chat only, no tools".

### 1.2 Design decisions in teknium1's follow-up comments

| Topic | Decision |
|---|---|
| Shared chats | Enforce **per message, by the current sender**, not "most restrictive user in the room". The agent may load the toolset of the highest tier present, but a lower-tier sender's tool call is **intercepted at dispatch**. |
| Speaker context | The current speaker's tier and allowed tools go into session context so the agent can explain refusals. |
| Migration | Existing allowlists map: **first entry ⇒ owner, rest ⇒ admin**; pairing-approved users ⇒ **user**. |
| Guest | Later comment proposes a *useful* guest (`web_search`, `web_extract`, `vision_analyze`, `clarify`, `text_to_speech`, ~10 req/h) — "useful, not crippled". This conflicts with the body's "no tools" and is still open (see §4.6). |
| Grants | Per-tool names plus `@group` shorthands (`@web`, `@read`, `@write`, `@code`, `@memory`, `@all`, `@safe`, …). |
| MCP | Default-deny below admin; explicit `mcp:<server>:<tool>` grants. |
| Modes | Tier = ceiling; future user-selected modes compose by **intersection**. |
| Workspace / uploads | Tier-aware at both the command and the system-prompt manifest level; `_build_system_prompt` needs the tier; a session is a *chat*, not a user. |
| UX | `/whoami`, `/promote`, specific denial messages. |

### 1.3 What actually landed

On 2026-07-26 teknium1 pointed #527 at the merged command-level split
("Implemented a simple version here: docs/reference/slash-commands#permissions-and-adminuser-split").
That is #23373, in this tree as `gateway/slash_access.py`:

- `SlashAccessPolicy` (`gateway/slash_access.py:31`) / `policy_for_source()` (`:100`): two lists per scope
  (`allow_admin_from`, `user_allowed_commands`, with `group_*` variants), DM and group independent,
  `help`/`whoami` always allowed, gating only active when `allow_admin_from` is set, fail-closed under
  multiplex.
- `/whoami` (`gateway/slash_commands.py`) reports the caller's scope and permitted commands.
- Enforced at gateway dispatch (`gateway/run_busy.py` `_check_slash_access`, `gateway/run_inbound.py`).

So the maintainer's stated direction is: **land small, generic, cross-platform slices; expand into tool
filtering etc. "as separate PRs when concrete need emerges".** Commenters on #527 (fuzhyperblue,
michaeltanyk, marek-commits) note that command gating does not cover tool access or memory/identity
scoping, that "Guest with `tools: []`" is the most-requested piece, and that unknown state must fail closed.
#110686 is exactly that remaining gap biting in production.

---

## 2. Why prior PRs did not merge

### PR #4443 (ReqX, closed, +16,133 / −3,086, 18 files, 448 tests)

What it was: the complete #527 vision in one PR — new `gateway/permissions.py` (~1,400 lines:
`PermissionManager`, SQLite runtime user store, rate limiting, i18n, platform role mapping, promote-request
store), new `gateway/audit.py` (SQLite audit log) and `gateway/usage.py` (token usage store), Telegram
`getChatMember` / Discord role extraction, `allowed_tool_names` at dispatch in `run_agent.py`, delegate
inheritance, MCP filtering in `model_tools.py`, `/promote` + `/audit`, time windows, per-user tool overrides,
plus edits across `gateway/run.py` (then a god file).

Why it was not merged — teknium1's closing comment (2026-05-10):

> closing this in favor of #23373 which just merged into main. We built off your design but scoped down to
> slash commands only for v1. The slimmer landing point keeps this surface minimal so we can re-expand into
> the rest of your tier work (tool filtering, audit log, usage tracking, rate limiting, /promote flow, MCP
> default-deny) as separate PRs when concrete need emerges.

The author's credit was preserved via `Co-authored-by:` on #23373. Reading that against the repo's contribution rubric
(`AGENTS.md`), the reasons are:

1. **Size / review cost.** 16k lines across security-sensitive dispatch, config, three new SQLite stores,
   and two platform adapters cannot be reviewed as one unit; the maintainer explicitly chose a "slimmer
   landing point".
2. **Speculative surface.** Audit log, usage tracking, rate limits, time windows, i18n, `/promote` flow had
   no concrete consumer yet ("when concrete need emerges"; rubric: "hooks with no concrete consumer" are
   rejected).
3. **Core growth instead of a narrow waist.** It added three persistent stores and grew `gateway/run.py`
   and `run_agent.py` rather than slotting into existing seams (the repo has since been split into
   facade + siblings, so the PR would also no longer apply).
4. **The minimal shape was different.** What landed is two lists, not four tiers, using the live command
   registry (`is_gateway_known_command()`), off unless configured.

Predecessor #3156 (+3,607) was closed by its author in favour of #4443.

### PR #22509 (alt-glitch, "Daimon" Discord bot, draft, open, `needs-decision`)

Discord-specific tiers, Docker sandbox, GitHub broker, per-thread caps. Its own description says it
"patches core directly" (`discord.py`, `run.py`). teknium1 closed it "in favor of #4443, which provides a
generic cross-platform permission tier system that the Discord-specific access control here would otherwise
duplicate"; the author reopened it as a personal working draft. Lesson: platform-specific access control
will not be accepted in place of a generic gateway mechanism.

### PR #58282 (elphamale, Telegram Mini App dashboard, open, +9,034)

Two tiers (paired / admin) for a **dashboard** authenticated by Telegram `initData`. Orthogonal to agent
tool/memory scoping; reviewer flagged the large surface area. Not a #527 implementation.

### PR #137975 (ewtillman, per-channel slash-command caps, open, no reviews)

Restrictive exact-chat-ID caps on slash commands, layered on #23373. Its own scope note: "not tool or memory
isolation". Overlaps #37021. Useful precedent for *exact IDs, fail-closed, restrictive-only* config shape.

### Takeaways for Phase 1

- Keep it small: one new small module, wire into existing seams, no new stores or commands.
- Generic across platforms; consume `SessionSource`, never adapter-specific roles in Phase 1.
- Off by default; byte-identical prompts and tool schemas when not configured.
- Address the gap teknium left open and the incident in #110686: **tool access and memory visibility**.

---

## 3. Current identity / tools / memory flow (traced)

```
platform adapter
  └─ build_source() ──► SessionSource (gateway/session.py:68)
        platform, chat_id, chat_type, user_id, user_id_alt, user_name, thread_id, ...
  └─ GatewayRunner._is_user_authorized_for_source  (gateway/run.py:3815, profile-scope wrapper)
        └─ AuthzMixin._is_user_authorized           (gateway/authz_mixin.py:614)
              └─ _principal_authorized               (:629)  → bool only (no tier)
  └─ slash commands → slash_access.policy_for_source → admin/user command split (commands only)
  └─ turn
        └─ _resolve_turn_toolsets(user_config, source, platform_key)   (gateway/run_turn.py:476)
              └─ _resolve_enabled_toolsets_for_source                    (:458) platform toolset
        └─ SessionExecution._resolve_turn_agent                         (gateway/session_execution.py:188)
              sig = _agent_config_signature(..., enabled_toolsets, user_id, user_id_alt)
                                                                      (gateway/run_agent_cache.py:101)
              └─ _build_fresh_agent                                     (gateway/session_execution.py:151)
                    AIAgent(enabled_toolsets=..., user_id=src.user_id, user_name=..., chat_*=...,
                            skip_memory=policy.ignore_rules)
                      └─ agent_init._load_tools  (agent/agent_init.py:1095) → valid_tool_names
                      └─ MemoryStore().load_from_disk()   (agent/agent_init.py ~1318-1340)
                           (tools/memory_tool_store.py:221) reads MEMORY.md + USER.md,
                           freezes _system_prompt_snapshot via _render_block (:638)
                      └─ external memory provider initialize(user_id=...) unless skip_memory
        └─ system prompt: build_system_prompt (agent/system_prompt.py:796)
              volatile tier (:779) ← _memory_parts (:516)
                 ← MemoryStore.format_for_system_prompt("memory"/"user")  (:618)  frozen snapshot
                 ← external provider block
              session context ← build_session_context_prompt (gateway/session.py:378)
        └─ tool calls: turn_tool_round rejects names not in agent.valid_tool_names
              (agent/turn_tool_round.py ~107) → model_tools.handle_function_call (:873)
              → registry.dispatch (tools/registry.py:936)  (no caller identity)
```

Findings that drive the design:

1. **Caller identity already reaches agent construction**, and `user_id`/`user_id_alt` already participate
   in the agent-cache signature (`run_agent_cache.py:101`; comment: Honcho freezes identity at init, so
   shared-thread keys rebuild per user). A tier derived from the same source can ride the same path.
2. **Authorization is a boolean.** There is no notion of "who is the operator" anywhere in the agent
   (KeyArgo's analysis in #110686).
3. **Memory is speaker-blind and frozen.** `load_from_disk()` renders *every* entry of `MEMORY.md` and
   `USER.md` into `_system_prompt_snapshot` at agent init; `format_for_system_prompt()` returns it unchanged
   for the life of the agent (and it is only rebuilt on compression). `USER.md` is the operator's profile —
   this is the #110686 leak ("who am I?" answered with the owner's profile to an arbitrary Discord user).
4. **"No tools" does not mean "no memory".** `agent_init` loads `MemoryStore` whenever `not skip_memory`,
   regardless of tool access. A guest with `enabled_toolsets=[]` would still get the full `MEMORY.md` /
   `USER.md` in its prompt.
5. **Tool results leak too, not just the prompt.** The `memory` tool returns `current_entries` from the
   unfiltered live list (`tools/memory_tool.py` `_validate_single_op`, the `remove`/`patch`-without-`old_text` inventory path) and can
   patch/remove owner entries; `session_search` searches all sessions in the profile's `SessionDB`, not the
   caller's. Prompt filtering alone is insufficient if a lower tier keeps these tools.
6. **`enabled_toolsets=[]` already means "no tools"** in `model_tools._select_tool_names` (`None` = all,
   list = exactly those), so Guest needs no new toolset mechanism. But
   `_agent_config_signature` collapses `None` and `[]` to the same value
   (`sorted(enabled_toolsets) if enabled_toolsets else []`), so the tier must be an explicit cache-key
   component rather than inferred from the toolset list.

---

## 4. Phase 1 proposal

Goal: close #110686-class leaks and deliver the "Guest = no tools" piece of #527, in a PR small enough to
review (~300–500 lines of code + tests), off by default, generic across platforms.

### 4.1 Scope

In:
- Tier resolution `OWNER > ADMIN > USER > GUEST` from config + existing authorization signals.
- **Guest: zero tools** (and therefore no memory/skills tools, no MCP).
- **Memory visibility per tier**, applied when the prompt snapshot is built (agent init), so lower-tier
  callers never receive owner-only `MEMORY.md` / `USER.md` entries in the prompt.
- Lower tiers lose the tools that expose unfiltered owner memory/history (`memory`, `session_search`).
- Caller tier in the session-context prompt; tier shown in `/whoami`.

Out (deferred, matching teknium's "separate PRs when concrete need emerges"): configurable per-tier tool
lists / `@group` expansion, the "useful guest" preset, MCP default-deny, rate limits, time windows, audit
log, usage tracking, `/promote`, platform role mapping, per-message dispatch interception in shared
sessions (Phase 2, see §4.5), BLOCKED tier (admission is already handled by `_is_user_authorized`),
external memory-provider entry filtering.

### 4.2 Config

```yaml
gateway:
  permissions:
    enabled: true            # absent / false ⇒ feature off, behaviour byte-identical to today
    default_tier: guest      # who is admitted but not listed (allow_all, chat grants, open groups)
    pairing_tier: user       # pairing-approved users
    users:                   # exact platform IDs only; no usernames/display names
      "telegram:123456": owner
      "discord:987654": admin
    memory:
      untagged_min_tier: owner   # visibility of existing, untagged entries (fail closed)
```

Resolution order (first match wins), mirroring teknium's migration rule:

1. `users["<platform>:<user_id>"]` (and `user_id_alt`, for adapters that carry both).
2. If `users` is empty: first entry of the platform allowlist (`<PLATFORM>_ALLOWED_USERS`) ⇒ owner, other
   allowlist entries ⇒ admin.
3. Pairing-store approved ⇒ `pairing_tier`.
4. Local/trusted sources (`source.platform == LOCAL`, trusted inbound platforms already short-circuited in
   `_principal_authorized`) ⇒ owner.
5. Anything else that passed `_is_user_authorized` ⇒ `default_tier`.
6. Missing `user_id`, malformed config, or any resolver error ⇒ `guest` (fail closed, per marek-commits).

### 4.3 Guest tier: no tools

- Toolset ceiling per tier (fixed in Phase 1, not configurable):
  - `owner`: unchanged platform toolsets.
  - `admin`, `user`: unchanged platform toolsets **minus** `memory` and `session_search` (they expose
    unfiltered owner data, §3 finding 5). Admin can be granted them back in Phase 2 via tool grants.
  - `guest`: `enabled_toolsets = []`, plus no external memory provider tools, no MCP, no skills index.
- Applied in `_resolve_turn_toolsets` so it reaches `AIAgent(enabled_toolsets=...)` before construction.
  Because `agent.valid_tool_names` is computed from that list, a hallucinated tool call is already rejected
  by `turn_tool_round` — no new dispatch code is needed for per-user sessions.
- Session-context line: `**Caller tier:** guest (no tools available)` so the agent can explain refusals.

### 4.4 Per-tier memory filtering at prompt-build time

**Entry visibility tag.** An entry may start with an inline marker, reusing the existing `§`-delimited
entry format so no file-format migration is needed:

```
[visible: user] Team standup is at 10:00 UTC.
§
[visible: guest] Project homepage is https://example.org.
§
Owner prefers dark mode.                      ← untagged ⇒ untagged_min_tier (owner)
```

- An entry is included iff `tier >= entry_min_tier`.
- **Untagged entries default to `owner`** (all existing memory was written by/for the operator; fail
  closed). `USER.md` is the operator profile, so the same default hides it entirely from non-owners —
  exactly the #110686 fix.
- The tag is stripped from the rendered prompt text.
- Unknown tag values ⇒ owner (fail closed).

**Where the filter runs.** At agent init, when the snapshot is frozen — not at every prompt build — so the
prompt-cache invariant holds (the snapshot is still frozen for the life of the agent; a different tier
gets a different agent via the cache key, §4.5):

- `MemoryStore.__init__()` gains a `visibility_tier` parameter (default `OWNER` ⇒ current behaviour),
  stored on the instance so every `load_from_disk()` call applies it. `load_from_disk()` filters the entry lists used for `_system_prompt_snapshot` only; the live lists (used by the memory
  tool for writes) stay complete, which is safe because only owner keeps the `memory` tool in Phase 1.
- `agent_init` passes `agent.caller_tier` into `MemoryStore(...)`; `AIAgent.__init__` accepts
  `caller_tier: str | None = None` (`None` ⇒ owner ⇒ unchanged for CLI/TUI/cron/ACP callers).
- Guest also forces `skip_memory=True` for the external provider (its prompt block and prefetch are
  speaker-blind today). Admin/user keep external providers that are already per-user scoped; filtering
  external provider content by tier is deferred.
- Compression rebuilds the prompt by calling `agent._memory_store.load_from_disk()` again
  (`agent/system_prompt.py` ~`:826`); because the tier lives on the store instance, the rebuilt snapshot
  stays filtered.

### 4.5 Shared sessions and caching

- `_agent_config_signature` gains a `caller_tier` component. Per-user sessions are unaffected beyond that.
  In shared sessions (`is_shared_multi_user_session`, e.g. `thread_sessions_per_user=False`) the signature
  already includes `user_id`, so today the agent is already rebuilt when the speaker changes; adding the
  tier keeps that behaviour and guarantees an owner-built agent (full memory, full tools) is never reused
  for a guest message.
- This is stricter than teknium's long-term "load highest tier, intercept at dispatch" design, but it is
  correct today with zero new dispatch code. Phase 2 can introduce the per-message dispatch intercept and
  a "shared-session memory floor" once tool grants exist. Memory can never be "intercepted" after it is in
  the prompt, so in that Phase 2 world a shared session's memory snapshot must be built at the lowest tier
  that can speak there; that constraint is recorded here so Phase 2 does not regress it.

### 4.6 Open decision for the maintainer

Guest = **no tools** (issue body, michaeltanyk, this proposal) vs. the **useful guest** preset (teknium's
later comment). Phase 1 ships no-tools as the fail-closed default because it needs no grant mechanism;
the useful-guest preset becomes a one-line opt-in once Phase 2 tool grants land. The PR description should
ask this explicitly.

### 4.7 Exact files and functions to change

| File | Function / symbol | Change |
|---|---|---|
| `gateway/permissions.py` (new, ~120 lines) | `Tier` (IntEnum), `PermissionsConfig`, `resolve_tier(source, config, *, pairing_approved, allowlist)`, `toolset_ceiling(tier, toolsets)`, `entry_min_tier(entry)` | Pure functions, no I/O, no state. |
| `gateway/config.py` | gateway config dataclass / loader | Parse `gateway.permissions` (absent ⇒ disabled). |
| `hermes_cli/config_defaults.py` | `DEFAULT_CONFIG` | Document the new keys (disabled by default); docs-drift check. |
| `gateway/authz_mixin.py` | new `_resolve_caller_tier(source)` next to `_principal_authorized` (`:629`) | Reuse the pairing-store / allowlist lookups already done there; return `OWNER` when permissions disabled. |
| `gateway/run_turn.py` | `_resolve_turn_toolsets` (`:476`); background agent build (~`:531`) | Apply `toolset_ceiling`; pass `caller_tier`. |
| `gateway/session_execution.py` | `_build_fresh_agent` (`:151`), `_resolve_turn_agent` (`:188`) | Pass `caller_tier` to `AIAgent`; force `skip_memory=True` for guest; add tier to signature call. |
| `gateway/run_agent_cache.py` | `_agent_config_signature` (`:101`) | Add `caller_tier` to the blob. |
| `gateway/session.py` | `SessionContext` (`:167`), `build_session_context_prompt` (`:378`) | Optional `caller_tier`; emit one "Caller tier" line only when permissions are enabled. |
| `agent/agent_init.py` | `AIAgent` init params (~`:2284`), memory block (~`:1318`) | Accept `caller_tier`; pass to `MemoryStore(visibility_tier=...)`. |
| `run_agent.py` | `AIAgent.__init__` facade signature | Forward `caller_tier`. |
| `tools/memory_tool_store.py` | `MemoryStore.__init__` (`:175`), `load_from_disk` (`:221`), `_render_block` (`:638`) | Filter snapshot entries by tier; strip `[visible: …]` tag in rendered text. |
| `gateway/slash_commands.py` | `_handle_whoami_command` | Show tier when permissions enabled. |
| `website/docs/user-guide/messaging/index.md` (or security page) | — | Document config, tag syntax, defaults. |

Explicitly **not** touched: `tools/registry.py` (no caller identity at dispatch in Phase 1),
`model_tools.py`, `toolsets.py` (no new toolset needed; `[]` already means none), platform adapters.

### 4.8 Test plan

All via `scripts/run_tests.sh`, behaviour contracts against a temp `HERMES_HOME`, new test files
(no growth of existing over-size test files).

`tests/gateway/test_permissions_resolve.py` (pure resolver):
1. Permissions absent ⇒ every source resolves to owner; `toolset_ceiling` returns its input unchanged.
2. Explicit `users` mapping wins over allowlist; `user_id_alt` match works.
3. Migration: empty `users` ⇒ first allowlist entry owner, the rest admin.
4. Pairing-approved ⇒ `pairing_tier`; unlisted but admitted (allow_all / chat grant) ⇒ `default_tier`.
5. Missing `user_id`, malformed tier name, malformed config ⇒ guest (fail closed).
6. Cross-platform isolation: `telegram:1` owner does not make `discord:1` owner.

`tests/tools/test_memory_tier_visibility.py` (real `MemoryStore`, temp `HERMES_HOME`):
7. Default `visibility_tier` ⇒ snapshot byte-identical to current behaviour (regression guard).
8. Guest/user/admin ⇒ untagged `MEMORY.md` entries and all untagged `USER.md` entries absent from
   `format_for_system_prompt()`; `[visible: user]` entries present for user/admin/owner, absent for guest.
9. Tag is stripped from rendered text; unknown tag ⇒ owner-only.
10. Live entry list is unchanged (owner writes after a filtered load do not drop hidden entries from disk).
11. A second `load_from_disk()` (the compression rebuild path) on a guest-tier store stays filtered.

`tests/gateway/test_permissions_agent_build.py` (gateway E2E with a fake adapter, real `AIAgent`
construction, model call stubbed):
12. Guest DM ⇒ agent has no tool schemas (`valid_tool_names` empty), system prompt contains no
    `MEMORY.md` / `USER.md` owner text and no external-provider block, and contains the "Caller tier: guest"
    line. **This is the #110686 repro: proven red on base, green after.**
13. User tier ⇒ platform toolset minus `memory` and `session_search`; owner-only memory absent.
14. Owner ⇒ tools and prompt identical to permissions-disabled run.
15. Shared thread (`thread_sessions_per_user=False`): owner message then guest message in the same
    session key ⇒ second turn does **not** reuse the owner-built cached agent (different signature), and the
    guest turn's prompt has no owner memory.
16. Model emits a tool call by name while guest ⇒ rejected by the existing unknown-tool path, handler never
    runs.
17. Permissions disabled ⇒ the system prompt, tool schemas and cache signature match `main` exactly (prompt
    cache invariant).

`tests/gateway/test_whoami_tier.py`:
18. `/whoami` shows the tier only when permissions are enabled.

Manual check: Discord/Telegram test bot, owner + second account at `default_tier: guest`, ask "who am I?"
and "run `ls`" from the second account.

---

## 5. Phase 2+ (not in this PR)

1. Per-tier tool grants with `@group` shorthand; useful-guest preset; return `memory`/`session_search` to
   admin by grant.
2. Per-message dispatch intercept in shared sessions (teknium's "highest tier loads, sender gated at
   dispatch"), with the shared-session memory floor from §4.5.
3. MCP default-deny below admin with `mcp:<server>:<tool>` grants.
4. Tier-aware workspace/upload manifests.
5. Per-entry tier on memory *writes* (who wrote it), entry scoping per chat (#28279) / per user (#11430),
   external provider filtering, signed identity (#112119), dashboard identity (#62549).
6. Rate limits, `/promote`, audit — only with a concrete consumer.
