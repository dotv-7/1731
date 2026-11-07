"""OpenAI-compatible scripted model: reports what Hermes put in its context, or requests a tool."""
import json, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT = os.environ.get("E2E_ROOT", os.path.expanduser("~/hermes-e2e/root"))
ARGS = {"read_file": {"path": f"{ROOT}/memories/MEMORY.md"}, "terminal": {"command": f"cat {ROOT}/memories/MEMORY.md"},
        "memory": {"action": "read", "target": "memory"}, "session_search": {"query": "secret"},
        "dotv_public_lookup": {"query": "Education"}, "vision_analyze": {"image_url": f"{ROOT}/x.png", "question": "?"},
        "skill_view": {"name": "x"}, "execute_code": {"code": f"print(open('{ROOT}/memories/MEMORY.md').read())"}}
def text(m):
    c = m.get("content")
    return c if isinstance(c, str) else " ".join(p.get("text", "") for p in (c or []) if isinstance(p, dict))
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code); self.send_header("content-type", ctype); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        self._send(200, json.dumps({"object": "list", "data": [{"id": "fake", "object": "model", "context_length": 128000}]}).encode())
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        msgs = req.get("messages", [])
        sys_txt = "\n".join(text(m) for m in msgs if m.get("role") == "system")
        tools = sorted(t["function"]["name"] for t in req.get("tools") or [])
        last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
        after = msgs[last_user + 1:]
        utext = text(msgs[last_user]) if last_user >= 0 else ""
        tool_results = [text(m) for m in after if m.get("role") == "tool"]
        want = utext.split("TRY:")[1].split()[0] if "TRY:" in utext else None
        if not utext.strip():
            msg = {"role": "assistant", "content": "ok"}
        elif want and not tool_results:
            msg = {"role": "assistant", "content": None, "tool_calls": [{"id": f"call_{int(time.time()*1000)}", "type": "function",
                   "function": {"name": want, "arguments": json.dumps(ARGS.get(want, {}))}}]}
        else:
            report = {"owner_canary_in_context": "OWNER_SECRET_CANARY" in json.dumps(msgs), "owner_context_file_in_context": "OWNER_CONTEXT_CANARY" in json.dumps(msgs), "public_canary_in_context": "PUBLIC_FACT_CANARY" in sys_txt,
                      "tools_offered": tools, "tool_result": tool_results[-1][:300] if tool_results else None}
            msg = {"role": "assistant", "content": "REPORT " + json.dumps(report, ensure_ascii=False)}
        fr = "tool_calls" if msg.get("tool_calls") else "stop"
        usage = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
        if req.get("stream"):
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
            delta = {"role": "assistant"}
            if msg.get("tool_calls"):
                delta["tool_calls"] = [dict(msg["tool_calls"][0], index=0)]
            else:
                delta["content"] = msg["content"]
            for ch in ({"delta": delta, "finish_reason": None}, {"delta": {}, "finish_reason": fr}):
                self.wfile.write(f"data: {json.dumps({'id': 'x', 'object': 'chat.completion.chunk', 'model': 'fake', 'choices': [dict(ch, index=0)]})}\n\n".encode())
            self.wfile.write(f"data: {json.dumps({'id': 'x', 'object': 'chat.completion.chunk', 'model': 'fake', 'choices': [], 'usage': usage})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self._send(200, json.dumps({"id": "x", "object": "chat.completion", "model": "fake", "choices": [{"index": 0, "message": msg, "finish_reason": fr}], "usage": usage}).encode())
ThreadingHTTPServer(("127.0.0.1", 8700), H).serve_forever()
