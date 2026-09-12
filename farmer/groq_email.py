"""Groq email magic-link lane (mail.tm + Stytch).

Groq lets you sign up with a plain email via a Stytch magic link. This is the
deterministic lane for headless VPS farming (no Google account needed):

    create/reuse a mail.tm inbox  ->  console.groq.com/login
    -> "Continue with email" -> submit -> poll inbox for the Stytch magic link
    -> open the link -> land logged-in on the Groq console.

Inboxes are pooled in data/groq_inboxes.json so a batch run reuses verified
addresses first (mail.tm keeps them alive) instead of re-registering every time.

Attach: in tree.py PASSO 3, if site.get("via_email"), call signup_with_email()
instead of the Google lane.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
import urllib.request
from urllib.error import HTTPError
from pathlib import Path

MAILTM = "https://api.mail.tm"
POOL = Path(__file__).parent.parent / "data" / "groq_inboxes.json"
PREF_DOMAIN = "uberip.com"  # proven on this VPS

_LOGIN = "https://console.groq.com/login"


# ---------------------------------------------------------------- mail.tm api
def _api(url: str, data=None, method: str = "GET", token: str | None = None,
         tries: int = 3) -> dict | list:
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(data).encode() if data is not None else None
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except Exception as e:  # 429/5xx -> retry; 4xx -> re-raise
            last = e
            if isinstance(e, HTTPError) and e.code < 500:
                raise
            time.sleep(2)
    raise last  # noqa: B904


def _new_inbox() -> tuple[str, str, str]:
    """Register a fresh mail.tm address. Returns (address, password, token)."""
    doms = _api(MAILTM + "/domains")
    doms = doms if isinstance(doms, list) else []
    dom = PREF_DOMAIN if any(str(d.get("domain")) == PREF_DOMAIN for d in doms) \
        else (doms[0]["domain"] if doms else "uberip.com")
    addr = pw = None
    for _ in range(3):
        addr = "gf" + secrets.token_hex(7) + "@" + dom
        pw = "Zz9x!" + secrets.token_hex(4)
        try:
            _api(MAILTM + "/accounts", {"address": addr, "password": pw}, method="POST")
            break
        except Exception:
            addr = None  # 422 name collision or rate-limit -> retry new name
    if not addr or not pw:
        raise RuntimeError("mail.tm account creation failed 3x (per-IP gate?)")
    tok = _api(MAILTM + "/token", {"address": addr, "password": pw}, method="POST")
    tok = tok["token"] if isinstance(tok, dict) else tok
    return addr, pw, tok


def _load_pool() -> list[dict]:
    if POOL.exists():
        try:
            return json.loads(POOL.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_pool(pool: list[dict]):
    POOL.parent.mkdir(parents=True, exist_ok=True)
    POOL.write_text(json.dumps(pool, indent=1), encoding="utf-8")


def _get_or_create_inbox() -> tuple[str, str, str]:
    """Reuse a pooled inbox whose token still works, else create a new one."""
    for entry in _load_pool():
        try:
            r = _api(MAILTM + "/token",
                     {"address": entry["address"], "password": entry["password"]},
                     method="POST")
            tok = r["token"] if isinstance(r, dict) else r
            return entry["address"], entry["password"], tok
        except Exception:
            continue
    addr, pw, tok = _new_inbox()
    pool = _load_pool()
    pool.append({"address": addr, "password": pw, "created": time.strftime("%Y-%m-%d %H:%M")})
    _save_pool(pool)
    return addr, pw, tok


def _peek_magic_link(token: str) -> str | None:
    """Scan the inbox (newest first) for a Stytch magic-link URL.

    CRITICAL: mail.tm's *text* rendering breaks the stytch token mid-way
    (URL truncated at ~90 chars), which makes Stytch answer redirect-error.
    The FULL token is only in the HTML href — read/URL-decode THAT first,
    and pick the candidate whose token param is longest (>=40 chars).
    """
    msgs = _api(MAILTM + "/messages", token=token)
    arr = msgs.get("hydra:member", []) if isinstance(msgs, dict) else []
    if not arr:
        return None
    best = None
    best_len = 0
    details = sorted(
        (_api(MAILTM + "/messages/" + str(m["id"]), token=token) for m in arr),
        key=lambda x: str(x.get("createdAt", "")), reverse=True)
    for d in details:
        htm = d.get("html") or ""
        txt = d.get("text") or ""
        if isinstance(htm, list):
            htm = "\n".join(htm)
        if isinstance(txt, list):
            txt = "\n".join(txt)
        cands = []
        cands += re.findall(r'href=["\']([^"\']*stytch\.com/v1/magic_links[^"\']*)["\']', htm)
        cands += re.findall(r"https://stytch\.com/v1/magic_links/redirect\?[^\s\"'<>]+", txt)
        for c in cands:
            c = c.replace("&amp;", "&")
            q = c.split("?", 1)[1] if "?" in c else ""
            t = dict((p.split("=", 1) + [""])[:2] for p in q.split("&") if "=" in p).get("token", "")
            if len(t) > best_len:
                best, best_len = c, len(t)
    return best if best_len >= 25 else None


# ------------------------------------------------------------- page helpers
async def _click_continue_email(page) -> bool:
    for sel in [
        "button:has-text('Continue with email')",
        "button:has-text('Sign up with email')",
        "[data-testid*='email' i] button, button[data-testid*='email' i]",
        "button:has-text('email')",
    ]:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=4000)
                return True
        except Exception:
            continue
    return False


async def _fill_email(page, address: str) -> bool:
    sel = "input[name=email], input[type=email], input[autocomplete=email]"
    try:
        loc = page.locator(sel).first
        await loc.wait_for(state="visible", timeout=8000)
        await loc.fill(address)
        return True
    except Exception:
        try:
            await page.fill("input", address)
            return True
        except Exception:
            return False


async def _click_submit(page) -> bool:
    for sel in ["button[type=submit]", "button:has-text('Continue')",
                "button:has-text('Send magic link')", "button:has-text('Log in')"]:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=4000)
                return True
        except Exception:
            continue
    return False


async def signup_with_email(ctx, page, log, address: str | None = None,
                            password: str | None = None) -> bool:
    """Full Groq magic-link signup on the given page. True = logged into console."""
    if address and password:
        try:
            r = _api(MAILTM + "/token", {"address": address, "password": password},
                     method="POST")
            token = r["token"] if isinstance(r, dict) else r
        except Exception as e:
            log.err("EMAIL", f"pool token rejected ({e})")
            return False
    else:
        address, password, token = _get_or_create_inbox()
    log.step("EMAIL", "inbox", address, "ok")

    # 1. login page
    try:
        await page.goto(_LOGIN, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        log.err("EMAIL", f"goto login {e}")
    await page.wait_for_timeout(2000)

    # 2. Continue with email
    if not await _click_continue_email(page):
        # maybe already on the email form (SPA) — try straight to fill
        log.err("EMAIL", "'Continue with email' assente, provo fill diretto")
    await page.wait_for_timeout(900)

    # 3. fill + submit
    if not await _fill_email(page, address):
        log.err("EMAIL", "field email non trovato")
        return False
    await page.wait_for_timeout(400)
    if not await _click_submit(page):
        log.err("EMAIL", "submit non trovato")
        return False

    # 4. poll for the magic link
    ml = None
    t0 = time.time()
    while time.time() - t0 < 75:
        await asyncio.sleep(4)
        try:
            ml = _peek_magic_link(token)
            if ml:
                break
        except Exception:
            continue
    if not ml:
        log.err("EMAIL", "magic link non arrivato in 75s (rate-limit signup?)")
        return False
    log.step("EMAIL", "magic link ricevuto", ml[:80] + "…", "ok")

    # 5. open the link and let Stytch authenticate
    try:
        await page.goto(ml, timeout=60000, wait_until="domcontentloaded")
    except Exception as e:
        log.err("EMAIL", f"goto magic link {e}")
    for _ in range(12):  # up to ~36s for stytch -> console redirect
        await page.wait_for_timeout(3000)
        u = (page.url or "").lower()
        if "stytch" not in u and "magic_links" not in u:
            break
    await page.wait_for_timeout(2500)

    url = page.url or ""
    ok = "groq.com" in url
    log.step("EMAIL", "login ok" if ok else "redirect non-console",
             url[:140] or "(about:blank)", "ok" if ok else "err")
    return ok