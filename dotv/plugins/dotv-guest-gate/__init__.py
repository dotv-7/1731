"""dotv guest gate: second line of defence for the public (Guest) profile.

The public profile already exposes only a small toolset via ``platform_toolsets``; this hook
refuses any other tool call (mis-configuration, plugin/MCP tools, background forks). Install it
only into the public profile's ``plugins/`` directory: plugin managers are per HERMES_HOME.
Any internal error blocks the call (fail-closed).
"""

from __future__ import annotations

import os
from typing import Any, Optional

DEFAULT_ALLOWED_TOOLS = frozenset({
    "web_search", "web_extract", "clarify", "vision_analyze", "image_generate", "text_to_speech",
})
ENV_ALLOWED = "DOTV_GUEST_ALLOWED_TOOLS"


def allowed_tools() -> frozenset[str]:
    """Allowlist from ``DOTV_GUEST_ALLOWED_TOOLS`` (comma-separated) or the default; it can only narrow
    the default, never widen it."""
    raw = os.environ.get(ENV_ALLOWED)
    if raw is None:
        return DEFAULT_ALLOWED_TOOLS
    requested = {t.strip() for t in raw.split(",") if t.strip()}
    return frozenset(requested & DEFAULT_ALLOWED_TOOLS)


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}


def _is_remote_url(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


def check(tool_name: str, args: Any) -> Optional[dict[str, str]]:
    if tool_name not in allowed_tools():
        return _block(f"Tool '{tool_name}' is not available in a guest session.")
    if tool_name == "vision_analyze" and not _is_remote_url((args or {}).get("image_url")):
        return _block("In a guest session vision_analyze only accepts http(s) image URLs.")
    return None


def on_pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict[str, str]]:
    try:
        return check(str(tool_name or ""), args if isinstance(args, dict) else {})
    except Exception:
        return _block("Guest gate error; tool call refused.")


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
