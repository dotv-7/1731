set -e
R=${E2E_ROOT:-$HOME/hermes-e2e/root}; P=$R/profiles/public; rm -rf $R; mkdir -p $R/memories $P/dotv $P/plugins
MODEL='model:
  default: fake
  provider: custom
  base_url: http://127.0.0.1:8700/v1
  api_key: dummy'
printf '%s\ngateway:\n  multiplex_profiles: true\nsessions:\n  auto_prune: false\n' "$MODEL" > $R/config.yaml
printf 'API_SERVER_ENABLED=true\nAPI_SERVER_PORT=8650\nAPI_SERVER_KEY=owner-e2e-key-0123456789abcdef0123\n' > $R/.env
printf 'OWNER_SECRET_CANARY: owner backup lives at /secret/path\n' > $R/memories/MEMORY.md
printf 'OWNER_SECRET_CANARY user profile: private relationship notes\n' > $R/memories/USER.md
D=$(cd "$(dirname "$0")/../.." && pwd)
cp -r $D/plugins/* $P/plugins/; cp $D/profiles/public/SOUL.md $P/
{ printf '%s\n' "$MODEL"; cat $D/profiles/public/config.yaml; } > $P/config.yaml
printf 'API_SERVER_KEY=public-e2e-key-fedcba9876543210fedc\n' > $P/.env
printf '{"version":1,"facts":["PUBLIC_FACT_CANARY: dotv writes a blog about agents."],"documents":[{"title":"Education","text":"Master degree (PUBLIC_DOC_CANARY)."}]}' > $P/dotv/public_context.json
echo setup-ok
