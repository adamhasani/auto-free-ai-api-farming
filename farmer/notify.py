"""Optional Telegram notifier — zero new dependencies (stdlib urllib only).

Enabled by setting both env vars (or OS-keyring entries):
    TELEGRAM_BOT_TOKEN   bot token from @BotFather
    TELEGRAM_CHAT_ID     chat/group id to deliver to

If the token is missing every call is a silent no-op, so the rest of the tool
behaves exactly as before; a run simply *also* pings your phone when configured
("monitorabili da telefono" without having to poll the VPS).
"""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.parse
import urllib.error

from . import secretstore

API = "https://api.telegram.org/bot{token}/sendMessage"


def config() -> tuple[str, str] | None:
    token = secretstore.get("telegram_token", env=("TELEGRAM_BOT_TOKEN",))
    chat = secretstore.get("telegram_chat", env=("TELEGRAM_CHAT_ID",))
    if token and chat:
        return token, chat
    return None


def send(text: str, silent: bool = True) -> bool:
    """POST one message via Bot API. Returns True on 200. Never raises for the caller."""
    cfg = config()
    if not cfg:
        return False
    token, chat = cfg
    body = json.dumps({
        "chat_id": chat,
        "text": text,
        "disable_notification": silent,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(API.format(token=token), data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def run_summary(results: dict, keys_new: list[str]) -> str:
    """Compact end-of-run digest: counts, per-site status, freshly harvested keys."""
    ok = [n for n, v in results.items() if v.get("status") == "ok"]
    wall = [n for n, v in results.items() if v.get("status") == "wall"]
    err = [n for n, v in results.items() if v.get("status") in ("error",)]
    lines = [
        "🌾 Farm done: "
        f"{len(ok)} ok · {len(wall)} wall · {len(err)} error",
    ]
    if keys_new:
        lines.append("🆕 keys:")
        for p in keys_new:
            lines.append(f"  • {p}")
    if err:
        lines.append("⚠️ errors: " + ", ".join(err[:8]))
    return "\n".join(lines)