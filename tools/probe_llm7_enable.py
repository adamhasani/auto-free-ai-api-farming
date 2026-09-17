"""Check if the LLM7 login form ENABLES over time (headed, under Xvfb)."""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=False)
        ctx = await b.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
        page = await ctx.new_page()
        await page.goto("https://token.llm7.io/", wait_until="domcontentloaded", timeout=45000)
        for n in range(11):  # 0..10 -> t=0,3,6,...30s
            if n: await page.wait_for_timeout(3000)
            st = await page.evaluate("""() => ({
              url: location.href.slice(0,60),
              emailDisabled: (() => { const e=document.querySelector('#email-address'); return e ? e.disabled : null; })(),
              oauthDisabled: [...document.querySelectorAll('button.oauth-provider-button')].map(x=>x.disabled),
              termsChecked: (() => { const c=document.querySelector('[role=checkbox]'); return c ? c.getAttribute('aria-checked') : null; })(),
              body: document.body.innerText.slice(0,240).replace(/\\n+/g,' | ')
            })""")
            print("STATE", n*3, "s", json.dumps(st, ensure_ascii=False), flush=True)
        await b.close()

asyncio.run(main())
