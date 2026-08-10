# -*- coding: utf-8 -*-
"""Quart API server: expose Outlook registration + CAPTCHA solving as HTTP endpoints.

Endpoints:
  POST /register        — register one Outlook account (async task)
  GET  /register/<id>   — poll task result
  POST /solve/turnstile — solve Cloudflare Turnstile for a target page
  POST /solve/arkose    — solve Arkose FunCaptcha via CapSolver/EZ-Captcha
  GET  /health          — liveness

Usage:
  OUTLOOK_NO_BITBROWSER=1 python api_server.py --port 9000
"""
import argparse
import asyncio
import os
import uuid
from datetime import datetime, timezone

from quart import Quart, jsonify, request

app = Quart(__name__)

TASKS: dict = {}
TASK_TTL_S = 3600


async def _ttl_sweep():
    """P0-1: evict tasks older than TTL — TASKS would grow unbounded otherwise."""
    import time as _time
    from datetime import datetime as _dt
    while True:
        await asyncio.sleep(300)
        now = _time.time()
        stale = [
            k for k, v in TASKS.items()
            if (now - _dt.fromisoformat(v.get("created", _dt.now(timezone.utc).isoformat())).timestamp()) > TASK_TTL_S
        ]
        for k in stale:
            TASKS.pop(k, None)


@app.before_serving
async def _start_sweep():
    asyncio.create_task(_ttl_sweep())


@app.get("/health")
async def health():
    return jsonify({"status": "ok", "ts": datetime.now(timezone.utc).isoformat()})


@app.post("/register")
async def register():
    """Queue one Outlook registration. Body: {count?, proxy?, mode?}"""
    data = await request.get_json(silent=True) or {}
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {"status": "queued", "created": datetime.now(timezone.utc).isoformat()}

    async def _run():
        from register_outlook import register_one, BitBrowserClient, DEFAULT_PROXIES

        os.environ.setdefault("OUTLOOK_NO_BITBROWSER", "1")
        proxy = data.get("proxy")
        mode = data.get("mode", "headless")
        if not proxy and not data.get("no_proxy"):
            proxy = DEFAULT_PROXIES[0] if DEFAULT_PROXIES else None
        bb = BitBrowserClient()
        results, lock = [], asyncio.Lock()
        try:
            email, password = await asyncio.wait_for(
                register_one(bb, 0, proxy, results, lock, mode=mode),
                timeout=data.get("timeout", 300),
            )
            TASKS[task_id].update({
                "status": "done",
                "email": email,
                "password": password,
                "result": results[0] if results else None,
            })
        except asyncio.TimeoutError:
            # P0-2: task masih jalan di background setelah wait_for timeout —
            # cancel biar browser tidak bocor, status final deterministik
            TASKS[task_id].update({"status": "timeout"})
        except Exception as exc:
            TASKS[task_id].update({"status": "error", "error": str(exc)[:300]})

    t = asyncio.create_task(_run())
    TASKS[task_id]["task"] = t
    try:
        await asyncio.wait_for(asyncio.shield(t), timeout=data.get("timeout", 300) + 30)
    except asyncio.TimeoutError:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
        TASKS[task_id].update({"status": "timeout"})
    return jsonify({"task_id": task_id, "status": "queued"})


@app.get("/register/<task_id>")
async def register_status(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    return jsonify(task)


@app.post("/solve/turnstile")
async def solve_turnstile():
    """Solve Cloudflare Turnstile. Body: {url, sitekey, action?, cdata?}"""
    data = await request.get_json(silent=True) or {}
    url = data.get("url")
    sitekey = data.get("sitekey")
    if not url or not sitekey:
        return jsonify({"error": "url and sitekey required"}), 400

    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {"status": "queued"}

    async def _run():
        from turnstile.solve import solve_route
        try:
            from patchright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
                page = await browser.new_page()
                token, _ = await solve_route(
                    page, url=url, sitekey=sitekey,
                    action=data.get("action"), cdata=data.get("cdata"),
                )
                await browser.close()
            TASKS[task_id].update({"status": "done", "token": token})
        except Exception as exc:
            TASKS[task_id].update({"status": "error", "error": str(exc)[:300]})

    asyncio.create_task(_run())
    return jsonify({"task_id": task_id, "status": "queued"})


@app.get("/solve/<task_id>")
async def solve_status(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    return jsonify(task)


@app.post("/solve/arkose")
async def solve_arkose():
    """Solve Arkose FunCaptcha via CapSolver/EZ-Captcha. Body: {public_key?}"""
    data = await request.get_json(silent=True) or {}
    public_key = data.get("public_key") or "B7D8911C-5CC8-A9A3-35B0-554ACEE604DA"
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {"status": "queued"}

    async def _run():
        from register_outlook import solve_arkose_capsolver, solve_funcaptcha_ezcaptcha
        try:
            token = await asyncio.to_thread(
                solve_arkose_capsolver, public_key=public_key,
                page_url=data.get("url", "https://signup.live.com/"),
                max_wait=data.get("max_wait", 120),
            )
            if not token:
                token = await asyncio.to_thread(
                    solve_funcaptcha_ezcaptcha, public_key=public_key,
                    page_url=data.get("url", "https://signup.live.com/"),
                    max_wait=data.get("max_wait", 120),
                )
            TASKS[task_id].update({"status": "done", "token": token})
        except Exception as exc:
            TASKS[task_id].update({"status": "error", "error": str(exc)[:300]})

    asyncio.create_task(_run())
    return jsonify({"task_id": task_id, "status": "queued"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
