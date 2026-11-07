import json, sys, urllib.request
B = "http://127.0.0.1:8650"
K = {"guest": ("/p/public", "public-e2e-key-fedcba9876543210fedc"), "owner": ("", "owner-e2e-key-0123456789abcdef0123")}
def ask(role, prompt):
    pre, key = K[role]
    req = urllib.request.Request(f"{B}{pre}/v1/chat/completions", data=json.dumps({"model": "hermes-agent", "messages": [{"role": "user", "content": prompt}]}).encode(),
                                 headers={"Authorization": f"Bearer {key}", "content-type": "application/json"})
    try:
        out = json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]["content"]
    except Exception as e:
        out = f"ERR {e} {getattr(e, 'read', lambda: b'')()[:200]}"
    print(f"[{role}] {prompt}: {out[:420]}\n")
for p in ["who is he", "TRY:read_file", "TRY:terminal", "TRY:execute_code", "TRY:memory", "TRY:session_search", "TRY:skill_view", "TRY:vision_analyze", "TRY:dotv_public_lookup"]:
    ask("guest", p)
ask("owner", "who am I")
