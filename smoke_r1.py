# -*- coding: utf-8 -*-
"""EVIDENCE R1 smoke: patchright headless launch -> load signup.live.com -> dump form elements + RAM."""
import asyncio, json, os, sys

sys.path.insert(0, "/home/ubuntu/outlook-autoreg")

async def main():
    def chrome_rss():
        import subprocess
        out = subprocess.run(["ps", "-eo", "pid,rss,comm"], capture_output=True, text=True).stdout
        rss = 0
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and "chrome" in parts[2]:
                rss += int(parts[1])
        return rss

    base_rss = chrome_rss()
    from patchright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = await browser.new_context()
    page = await ctx.new_page()

    # capture status/title — last doc response for signup.live.com (initial is 302 redirect)
    status, final_url = None, None
    def _on_resp(r):
        nonlocal status, final_url
        if "signup.live.com" in r.url and r.request.resource_type == "document":
            status = r.status
            final_url = r.url
    page.on("response", _on_resp)
    await page.goto("https://signup.live.com/", wait_until="domcontentloaded", timeout=45000)
    await asyncio.sleep(5)
    title = await page.title()

    ram_mb = round((chrome_rss() - base_rss) / 1024, 1)

    dump = await page.evaluate("""() => {
        const out = {inputs: [], selects: [], buttons: []};
        document.querySelectorAll('input').forEach(i => {
            out.inputs.push({type: i.type, name: i.name, id: i.id, placeholder: i.placeholder, visible: !!(i.offsetWidth || i.offsetHeight)});
        });
        document.querySelectorAll('select').forEach(s => {
            out.selects.push({name: s.name, id: s.id, opts: [...s.options].map(o => o.value)});
        });
        document.querySelectorAll('button, input[type=submit]').forEach(b => {
            if (b.offsetWidth || b.offsetHeight)
                out.buttons.push({tag: b.tagName, id: b.id, type: b.type, text: (b.innerText||b.value||'').trim().slice(0,40)});
        });
        out.hasMemberName = !!document.querySelector('[name=MemberName], #MemberName');
        out.bodySnippet = document.body.innerText.slice(0, 300).replace(/\\n+/g, ' | ');
        return out;
    }""")

    print("STATUS:", status)
    print("TITLE:", title)
    print("RAM_MB:", ram_mb)
    print("DUMP:", json.dumps(dump, ensure_ascii=False, indent=1))

    await browser.close()
    await pw.stop()

asyncio.run(main())
