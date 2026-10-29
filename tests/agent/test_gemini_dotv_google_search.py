"""dotv fork patch: HERMES_GEMINI_GOOGLE_SEARCH adds Google Search grounding to Gemini requests."""
from agent.gemini_native_adapter import build_gemini_request

MSGS = [{"role": "user", "content": "hi"}]
TOOLS = [{"type": "function", "function": {"name": "lookup", "description": "d", "parameters": {"type": "object", "properties": {}}}}]


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("HERMES_GEMINI_GOOGLE_SEARCH", raising=False)
    req = build_gemini_request(messages=MSGS, tools=TOOLS, model="gemini-3-pro-preview")
    assert {"googleSearch": {}} not in req["tools"]
    assert "includeServerSideToolInvocations" not in req.get("toolConfig", {})


def test_enabled_with_function_tools(monkeypatch):
    monkeypatch.setenv("HERMES_GEMINI_GOOGLE_SEARCH", "true")
    req = build_gemini_request(messages=MSGS, tools=TOOLS, model="gemini-3-pro-preview")
    assert req["tools"][-1] == {"googleSearch": {}}
    assert any("functionDeclarations" in t for t in req["tools"])
    assert req["toolConfig"]["includeServerSideToolInvocations"] is True


def test_enabled_without_function_tools(monkeypatch):
    monkeypatch.setenv("HERMES_GEMINI_GOOGLE_SEARCH", "1")
    req = build_gemini_request(messages=MSGS, model="gemini-3-pro-preview")
    assert req["tools"] == [{"googleSearch": {}}]
    assert "toolConfig" not in req
