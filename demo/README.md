# Demo: Salt

Everything in this folder is the demo shown at https://527.dotv.io/. None of it ships in the plugin.

Salt is a fictional pseudonymous on-chain analyst; her owner, Lena, is fiction too. Every fact,
password and address in the seed data is invented.

| Path | What |
|---|---|
| `SOUL.md` | Salt's public persona, used instead of the generic template in `salts_secrets/profile/` |
| `seed_demo.py` | Writes a demo owner home (memories, workspace, skills) to play with |
| `chat/server.py` | Web chat with a passphrase-unlocked owner mode, plus the original `/shelf`, `/perm`, `/trace` pages |
| `fake_hermes.py` | A stand-in for the Hermes API server, so the chat runs without a model |
| `trace_run.py`, `trace_render.py` | Ask the same questions to both profiles and render every byte each received |
| `e2e/` | Probe script against a real gateway with a fake LLM |

## Run it locally

```bash
pip install -r demo/requirements-dev.txt
python3 demo/seed_demo.py "$PWD/.demo/owner"
DOTV_OWNER_HOME="$PWD/.demo/owner" DOTV_OWNER_PASSPHRASE_HASH=... DOTV_SECRET=<random> \
  python3 demo/chat/server.py          # http://127.0.0.1:8780/
```

`DOTV_OWNER_HOME` defaults to `~/.hermes`; `DOTV_PUBLIC_HOME` defaults to
`$DOTV_OWNER_HOME/profiles/public`. Without `DOTV_HERMES_URL` the chat talks to
`http://127.0.0.1:8642`, the Hermes API server (or `fake_hermes.py`).
`DOTV_OWNER_NAME` and `DOTV_PUBLIC_NAME` (default `Lena` and `Salt`) set the names the pages show.

## Traces

`trace_render.py` replaces the owner home, the user's home directory and every loopback URL with
placeholders before writing anything under `chat/static/trace/`. Regenerate a trace after changing
the demo data instead of editing the files by hand.
