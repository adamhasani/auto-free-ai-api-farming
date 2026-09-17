"""Probe LLM7 (token.llm7.io -> dash.llm7.io) email lane live, headless.

Shows: page state over time, whether email input enables, the magic-link email,
what the post-login page looks like (API key location). Fresh browser profile.
"""
import asyncio, json, re, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from playwright.async_api import async_playwright
from farmer.groq_email import _new_inbox

async def probe():
    addr, pw, tok = _new_inbox()
    print("INBOX", addr, flush=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=False)
        ctx = await b.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
        page = await ctx.new_page()
        await page.goto("https://token.llm7.io/", wait_until="domcontentloaded", timeout=45000)
        for n in range(5):
            await page.wait_for_timeout((0 if n == 0 else 4) * 1000)
            st = await page.evaluate("""() => ({
              url: location.href,
              title: document.title,
              emailDisabled: (() => { const e=document.querySelector('#email-address'); return e ? e.disabled : null; })(),
              emailVisible: (() => { const e=document.querySelector('#email-address'); return e ? getComputedStyle(e).display!=='none' : false; })(),
              oauthDisabled: [...document.querySelectorAll('button.oauth-provider-button')].map(x=>x.disabled),
              termsChecked: (() => { const c=document.querySelector('[role=checkbox]'); return c ? c.getAttribute('aria-checked') : null; })(),
              body: document.body.innerText.slice(0,500).replace(/\\n+/g,' | ')
            })""")
            print("STATE", n, json.dumps(st, ensure_ascii=False)[:500], flush=True)
            if st["emailDisabled"] is False:
                break
        # --- fill email + click Continue with email ---
        filled = False
        try:
            el = page.locator("#email-address")
            await el.wait_for(state="visible", timeout=8000)
            try:
                await el.click(timeout=2000); await el.fill(addr)
            except Exception:
                await page.evaluate(f"document.querySelector('#email-address').value='{addr}'")
            await page.wait_for_timeout(500)
            val = await page.evaluate("document.querySelector('#email-address').value")
            print("EMAILFIELD", repr(val), flush=True)
            clicked = None
            for sel in ["button:has-text('Continue with email')",
                        "button:has-text('Continue')"]:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    try:
                        await loc.click(timeout=3000)
                        clicked = sel
                        break
                    except Exception as e:
                        print("CLICKERR", sel, str(e)[:120], flush=True)
                        try:
                            await page.evaluate(f"(() => {{ const b=[...document.querySelectorAll('button')].find(x=>x.innerText.includes('Continue with email')); if(b){{ b.disabled=false; b.click(); }} }})()")
                            clicked = sel
                        except Exception:
                            pass
                        break
            print("CLICKED", clicked, flush=True)
            filled = True
        except Exception as e:
            print("FILLERR", str(e)[:200], flush=True)
        await page.wait_for_timeout(3500)
        print("AFTER", json.dumps(await page.evaluate("""() => ({url:location.href, body:document.body.innerText.slice(0,400).replace(/\\n+/g,' | '), hasErr: /error|invalid|already/i.test(document.body.innerText.slice(0,600))})"""), ensure_ascii=False), flush=True)

        # --- poll mail.tm inbox for the LLM7 email (up to 120s) ---
        from farmer.groq_email import _api, MAILTM
        found = None
        for _ in range(30):
            await page.wait_for_timeout(4000)
            try:
                msgs = _api(MAILTM + "/messages", token=tok)
                arr = msgs.get("hydra:member", []) if isinstance(msgs, dict) else []
            except Exception as e:
                print("POLLAPI-ERR", str(e)[:80], flush=True); continue
            if arr:
                latest = arr[0]
                d = _api(MAILTM + "/messages/" + str(latest["id"]), token=tok)
                subj = str(d.get("subject", ""))
                html = "".join(d.get("html") or []) if isinstance(d.get("html"), list) else (d.get("html") or "")
                txt = "".join(d.get("text") or []) if isinstance(d.get("text"), list) else (d.get("text") or "")
                print("MAIL", json.dumps({"from": d.get("from"), "subject": subj, "html_len": len(html), "txt_len": len(txt)}), flush=True)
                links = re.findall(r'https?://[^\s"\'<>]+', html + "\n" + txt)
                found = (d, html, txt, links)
                break
        if not found:
            print("NO MAIL in 120s", flush=True)
            await b.close(); return
        d, html, txt, links = found
        ml = [l for l in links if "llm7" in l and ("auth" in l or "token" in l or "login" in l or "magic" in l)]
        print("MAGICLINKS", json.dumps(ml[:5]) if ml else "none; all=" + json.dumps(links[:8]), flush=True)
        # --- open first plausible link ---
        target = ml[0] if ml else (links[0] if links else None)
        if not target:
            print("NO LINK", flush=True); await b.close(); return
        p2 = await ctx.new_page()
        try:
            await p2.goto(target, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print("GOTO-ERR", str(e)[:120], flush=True)
        await p2.wait_for_timeout(6000)
        print("FINAL", json.dumps(await p2.evaluate("""() => ({url:location.href, title:document.title, body:document.body.innerText.slice(0,900).replace(/\\n+/g,' | ')})"""), ensure_ascii=False), flush=True)
        await b.close()

asyncio.run(probe())