# -*- coding: utf-8 -*-
"""Quart API server: expose Outlook registration + CAPTCHA solving as HTTP endpoints.

Endpoints:
  POST /register        — queue one Outlook registration (non-blocking, 202)
  GET  /register/<id>   — poll task result
  POST /solve/turnstile — solve Cloudflare Turnstile for a target page
  POST /solve/arkose    — solve Arkose FunCaptcha via CapSolver/EZ-Captcha
  GET  /solve/<id>      — poll solve task result
  GET  /health          — liveness

Auth (optional): set API_TOKEN env — then all endpoints require
  Authorization: Bearer <token> (constant-time compare).
Rate limit: single global semaphore (REG_MAX_CONCURRENCY, default 2) —
  extra requests queue, not spawn unbounded browsers.

Usage:
  API_TOKEN=secret OUTLOOK_NO_BITBROWSER=1 python api_server.py --port 9000
"""
import argparse
import asyncio
import hmac
import os
import uuid
from datetime import datetime, timezone

from quart import Quart, jsonify, request

app = Quart(__name__)

TASKS: dict = {}          # public state (json-safe)
_TASK_HANDLES: dict = {}  # asyncio.Task handles (never jsonified) — T-01
TASK_TTL_S = 3600

API_TOKEN = os.environ.get("API_TOKEN", "")
_MAX_CONCURRENCY = max(1, int(os.environ.get("REG_MAX_CONCURRENCY", "2")))
_sem = asyncio.Semaphore(_MAX_CONCURRENCY)


def _now():
    return datetime.now(timezone.utc).isoformat()


async def _check_auth():
    if not API_TOKEN:
        return None
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return jsonify({"error": "unauthorized"}), 401
    supplied = header[7:]
    if not hmac.compare_digest(supplied, API_TOKEN):
        return jsonify({"error": "unauthorized"}), 401
    return None


async def _ttl_sweep():
    """Evict tasks older than TTL (fix r1; now covers /solve/* too — T-05)."""
    import time as _time
    while True:
        await asyncio.sleep(300)
        now = _time.time()
        stale = []
        for k, v in TASKS.items():
            try:
                created = _time.mktime(datetime.fromisoformat(v.get("created", "")).timetuple())
            except Exception:
                created = now
            if now - created > TASK_TTL_S:
                stale.append(k)
        for k in stale:
            TASKS.pop(k, None)
            _TASK_HANDLES.pop(k, None)


@app.before_serving
async def _start_sweep():
    asyncio.create_task(_ttl_sweep())


@app.get("/health")
async def health():
    return jsonify({"status": "ok", "ts": _now()})


@app.post("/register")
async def register():
    """Queue one Outlook registration. Non-blocking: returns 202 immediately."""
    auth_err = await _check_auth()
    if auth_err:
        return auth_err
    data = await request.get_json(silent=True) or {}
    timeout = data.get("timeout", 300)
    if not isinstance(timeout, (int, float)) or not (10 <= timeout <= 600):
        return jsonify({"error": "timeout must be 10..600"}), 400  # T-03

    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {"status": "queued", "created": _now()}

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
            async with _sem:  # T-03: bounded concurrency
                email, password = await asyncio.wait_for(
                    register_one(bb, 0, proxy, results, lock, mode=mode),
                    timeout=timeout,
                )
            TASKS[task_id].update({
                "status": "done",
                "email": email,
                "password": password,
                "result": results[0] if results else None,
            })
        except asyncio.TimeoutError:
            TASKS[task_id].update({"status": "timeout"})
        except Exception as exc:
            TASKS[task_id].update({"status": "error", "error": str(exc)[:300]})
        finally:
            _TASK_HANDLES.pop(task_id, None)

    t = asyncio.create_task(_run())
    _TASK_HANDLES[task_id] = t  # T-01: handle terpisah, tidak masuk TASKS
    return jsonify({"task_id": task_id, "status": "queued"}), 202


@app.get("/register/<task_id>")
async def register_status(task_id: str):
    auth_err = await _check_auth()
    if auth_err:
        return auth_err
    task = TASKS.get(task_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    return jsonify(task)


@app.post("/solve/turnstile")
async def solve_turnstile():
    """Solve Cloudflare Turnstile. Body: {url, sitekey, action?, cdata?}"""
    auth_err = await _check_auth()
    if auth_err:
        return auth_err
    data = await request.get_json(silent=True) or {}
    url = data.get("url")
    sitekey = data.get("sitekey")
    if not url or not sitekey:
        return jsonify({"error": "url and sitekey required"}), 400

    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {"status": "queued", "created": _now()}  # T-05

    async def _run():
        from turnstile.solve import solve_route
        try:
            async with _sem:
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
        finally:
            _TASK_HANDLES.pop(task_id, None)

    t = asyncio.create_task(_run())
    _TASK_HANDLES[task_id] = t
    return jsonify({"task_id": task_id, "status": "queued"}), 202


@app.get("/solve/<task_id>")
async def solve_status(task_id: str):
    auth_err = await _check_auth()
    if auth_err:
        return auth_err
    task = TASKS.get(task_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    return jsonify(task)


@app.post("/solve/arkose")
async def solve_arkose():
    """Solve Arkose FunCaptcha via CapSolver/EZ-Captcha. Body: {public_key?}"""
    auth_err = await _check_auth()
    if auth_err:
        return auth_err
    data = await request.get_json(silent=True) or {}
    public_key = data.get("public_key") or os.environ.get(
        "ARKOSE_PUBLIC_KEY", "B7D8911C-5CC8-A9A3-35B0-554ACEE604DA")  # T-09
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {"status": "queued", "created": _now()}  # T-05

    async def _run():
        from register_outlook import solve_arkose_capsolver, solve_funcaptcha_ezcaptcha
        try:
            async with _sem:
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
        finally:
            _TASK_HANDLES.pop(task_id, None)

    t = asyncio.create_task(_run())
    _TASK_HANDLES[task_id] = t
    return jsonify({"task_id": task_id, "status": "queued"}), 202


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
