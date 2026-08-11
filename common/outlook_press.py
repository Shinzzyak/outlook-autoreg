# -*- coding: utf-8 -*-
"""Shared Outlook PerimeterX press-and-hold behavior.

Registration and recovery must use this module together. Keeping target
selection and the physical press sequence in one place prevents the two flows
from slowly diverging as Microsoft's challenge markup changes.
"""

from __future__ import annotations

import asyncio
import os
import random

from common import human_mouse


async def captcha_visible(page):
    """Return whether an interactive Outlook hold challenge is still visible."""
    try:
        for selector in (
            'button:has-text("Press and hold")',
            'button:has-text("Appuyer et maintenir")',
            'button:has-text("按住")',
            'button:has-text("长按")',
            'button:has-text("Halten")',
            '#px-captcha',
        ):
            element = page.locator(selector).first
            if await element.count() > 0:
                box = await element.bounding_box()
                if box and box["width"] > 30:
                    return True

        frames = page.locator(
            'iframe[src*="hsprotect.net"]'
        )
        for index in range(await frames.count()):
            box = await frames.nth(index).bounding_box()
            if box and box["width"] > 50 and box["height"] > 30:
                return True
    except Exception:
        pass
    return False


async def find_hold_target(page):
    """Use the target lookup proven by the Outlook registration flow.
    R25-F1: verifikasi nested iframe visible — #px-captcha sering DIV kosong
    (inner iframe display:none). Cari tombol di dalam child frame dulu.
    Return (box, is_button, target_frame) — target_frame utk press via frame."""
    # 1) child frames: cari tombol press-and-hold di dalam iframe hsprotect
    #    (URL frame challenge pakai ch_ctx=1, bukan "challenge")
    #    R25-F2: pilih tombol TERBESAR di frame — button[role="button"] pertama
    #    sering tombol aksesibilitas kecil (tooltip "Accessible challenge"),
    #    bukan tombol HIP utama. Tombol HIP jauh lebih lebar (>200px).
    for frame in page.frames:
        if "hsprotect.net" not in (frame.url or ""):
            continue
        if "challenge" not in (frame.url or "") and "ch_ctx" not in (frame.url or ""):
            continue
        best = None
        best_area = 0
        for sel in ('button[role="button"]', "#px-captcha", 'button:has-text("Press and hold")', 'button:has-text("按住")'):
            try:
                els = frame.locator(sel)
                n = await els.count()
                for i in range(min(n, 8)):
                    el = els.nth(i)
                    box = await el.bounding_box()
                    if not box or box["width"] < 30 or box["height"] < 8:
                        continue
                    area = box["width"] * box["height"]
                    if area > best_area:
                        best_area = area
                        best = (box, True, frame)
            except Exception:
                pass
        if best:
            return best

    # 2) fallback: frame hsprotect yang visible
    for frame in page.frames:
        if "hsprotect.net" not in (frame.url or ""):
            continue
        try:
            # nested iframe yang TIDAK display:none
            for i in range(await frame.locator("iframe").count()):
                iframe = frame.locator("iframe").nth(i)
                box = await iframe.bounding_box()
                if box and box["width"] > 30 and box["height"] > 8:
                    # cek display style
                    disp = await iframe.evaluate("el => getComputedStyle(el).display")
                    if disp != "none":
                        return box, True, frame
        except Exception:
            pass
        try:
            box = await frame.locator("#px-captcha").first.bounding_box()
            if box and box["width"] > 30 and box["height"] > 8:
                # R25-F1c: #px-captcha visible = tombol HIP (rendered captcha.js).
                # JANGAN cek inner iframe — di HIP baru inner iframe selalu
                # display:none (fallback lama), ngecek malah block return.
                return box, True, frame
        except Exception:
            pass

    # 3) main frame: button langsung (accessible challenge)
    try:
        for sel in (
            'button:has-text("Press and hold")',
            'button:has-text("Appuyer et maintenir")',
            'button:has-text("按住")',
            'button:has-text("长按")',
            'button:has-text("Press and")',
            'button:has-text("hold")',
            'button[data-testid*="hold" i]',
            'button[id*="hold" i]',
            'button[class*="hold" i]',
            'button[class*="captcha" i]',
            '[role="button"]:has-text("hold")',
        ):
            el = page.locator(sel).first
            if await el.count() > 0:
                box = await el.bounding_box()
                if box and box["width"] > 30 and box["height"] > 8:
                    return box, True, None
    except Exception:
        pass
    # 3b) shadow DOM: HIP render pakai web component — pierce open shadow roots
    try:
        btn = await page.evaluate("""() => {
            const hosts = document.querySelectorAll('*');
            for (const h of hosts) {
                if (!h.shadowRoot) continue;
                const b = h.shadowRoot.querySelector('button');
                if (b) {
                    const r = b.getBoundingClientRect();
                    if (r.width > 30 && r.height > 8) return {x: r.x, y: r.y, w: r.width, h: r.height};
                }
            }
            return null;
        }""")
        if btn:
            return {"x": btn["x"], "y": btn["y"], "width": btn["w"], "height": btn["h"]}, True, None
    except Exception:
        pass

    # 4) fallback lama: iframe hsprotect bounding box
    try:
        frames = page.locator('iframe[src*="hsprotect.net"]')
        for index in range(await frames.count()):
            box = await frames.nth(index).bounding_box()
            if box and box["width"] > 50 and box["height"] > 30:
                return box, False, None
    except Exception:
        pass
    return None, False, None


async def press_and_hold(page, *, label="", press_number=1):
    """Run one registration-style hold attempt, or return None without a target."""
    target_box, box_is_button, target_frame = await find_hold_target(page)
    if not target_box:
        return None

    bx = target_box["x"]
    by = target_box["y"]
    bw = target_box["width"]
    bh = target_box["height"]
    if box_is_button:
        cx = bx + bw * random.uniform(0.40, 0.60)
        cy = by + bh * random.uniform(0.40, 0.60)
    else:
        cx = bx + bw * random.uniform(0.42, 0.58)
        cy = by + bh * random.uniform(0.48, 0.62)

    suffix = " [btn]" if box_is_button else " [box]"
    print(f"{label} press #{press_number}: ({cx:.0f},{cy:.0f}){suffix}")

    # R25-F7: debug — elementFromPoint di koordinat press (cek overlay)
    if os.environ.get("OUTLOOK_DUMP_DOM") == "1" and target_frame is not None:
        try:
            # koordinat frame-relative: cari elemen target di frame, hitung
            # titik tengahnya, lalu elementFromPoint di titik itu
            hit = await target_frame.evaluate("""() => {
                const el = document.querySelector('#px-captcha, button[role="button"]');
                if (!el) return 'no target';
                const r = el.getBoundingClientRect();
                const x = r.x + r.width * 0.5;
                const y = r.y + r.height * 0.5;
                const hitEl = document.elementFromPoint(x, y);
                if (!hitEl) return 'null at ' + Math.round(x) + ',' + Math.round(y);
                let chain = [];
                let cur = hitEl;
                while (cur && cur.tagName) {
                    chain.push(cur.tagName + (cur.id ? '#' + cur.id : '') + (cur.className ? '.' + String(cur.className).slice(0,30) : ''));
                    cur = cur.parentElement;
                }
                const hr = hitEl.getBoundingClientRect();
                return {at: Math.round(x) + ',' + Math.round(y), tag: hitEl.tagName, id: hitEl.id, cls: String(hitEl.className).slice(0,50), chain: chain.join(' < '), box: Math.round(hr.x) + ',' + Math.round(hr.y) + ' ' + Math.round(hr.width) + 'x' + Math.round(hr.height)};
            }""")
            print(f"{label} elementFromPoint (frame-rel): {hit}")
        except Exception as e:
            print(f"{label} elementFromPoint err: {e}")

    async def hold_done():
        # R25-F1g: deteksi visual done — class btn_done / #checkmark di frame
        # hsprotect (captcha hilang = TERLAMBAT, ge() sudah jalan duluan).
        # Fallback: POST /ocaptcha (sinyal sukses) atau captcha hilang.
        try:
            if post_seen["ocaptcha"] > 0:
                return True
            for f in page.frames:
                if "hsprotect.net" not in (f.url or ""):
                    continue
                try:
                    done = await f.locator(
                        ".btn_done, #checkmark, .px-captcha-done, [class*='done']"
                    ).count()
                    if done and await f.locator(".btn_done, #checkmark, .px-captcha-done").count() > 0:
                        return True
                except Exception:
                    pass
        except Exception:
            pass
        return not await captcha_visible(page)

    # R25-F1b: monitor POST ke collector hsprotect — kalau 0 POST dalam 3s,
    # target salah (event tidak sampai) → abort cepat, jangan hold buta.
    post_seen = {"n": 0, "ocaptcha": 0}
    original_send = None

    # R25-F10: console + pageerror listener (script HIP bisa silent-fail)
    if os.environ.get("OUTLOOK_DUMP_DOM") == "1":
        try:
            page.on("console", lambda msg: print(f"{label} [console:{msg.type}] {msg.text[:150]}") if msg.type in ("error", "warning") else None)
            page.on("pageerror", lambda exc: print(f"{label} [pageerror] {str(exc)[:200]}"))
        except Exception:
            pass

    async def _monitor(resp):
        try:
            url = resp.url or ""
            if resp.request.method == "POST" and ("hsprotect" in url or "px-captcha" in url or "captcha" in url):
                post_seen["n"] += 1
                if "ocaptcha" in url:
                    post_seen["ocaptcha"] += 1
                # R25-F5: debug — log SEMUA POST captcha (filter lama
                # "hsprotect.net/api" mungkin salah — collector bisa di
                # subdomain/path lain)
                print(f"{label} POST {resp.request.method} {url[:120]}")
            # R25-F9: log SEMUA request ke hsprotect (GET/POST) — tahu apakah
            # script HIP jalan (harusnya ada GET init saat load)
            if "hsprotect.net" in url and resp.request.method == "GET":
                print(f"{label} GET {url[:120]}")
        except Exception:
            pass

    try:
        page.on("response", _monitor)
    except Exception:
        pass

    # R25-F1g (HUMAN deobf): hold TANPA tremor — mouseout/mouseleave = EVENT END
    # (Du). Tremor OU ±1.6px bikin cursor keluar bounds tombol → bar drain/reset.
    # Release HANYA setelah done (class btn_done / #checkmark / POST ocaptcha).
    # R25-F3: pakai CDP Input.dispatchMouseEvent — page.mouse TIDAK tembus
    # cross-origin iframe hsprotect di headless (0 POST collector).
    cdp = None
    frame_relative = False
    cdp_x = cdp_y = None
    # R25-F13: OUTLOOK_CDP=0 → pakai page.mouse (browser hit-testing, tembus
    # iframe di headed mode). CDP frame session belum terbukti generate
    # PointerEvent yang benar di OOPIF.
    if os.environ.get("OUTLOOK_CDP") != "0":
        try:
            if target_frame is not None:
                # R25-F6: attach CDP ke FRAME hsprotect langsung — event di-generate
                # di konteks frame (tembus OOPIF). bounding_box() Playwright =
                # viewport-relative; CDP frame session butuh frame-relative →
                # hitung via getBoundingClientRect di dalam frame.
                cdp = await page.context.new_cdp_session(target_frame)
                frame_relative = True
                try:
                    fr = await target_frame.evaluate("""() => {
                        const el = document.querySelector('#px-captcha, button[role="button"]');
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        return {x: r.x, y: r.y, w: r.width, h: r.height};
                    }""")
                    if fr and fr["w"] > 10:
                        cdp_x = fr["x"] + fr["w"] * 0.5
                        cdp_y = fr["y"] + fr["h"] * 0.5
                except Exception:
                    pass
            else:
                cdp = await page.context.new_cdp_session(page)
        except Exception:
            cdp = None
    try:
        held, passed = await human_mouse.human_press_and_hold(
            page,
            cx,
            cy,
            is_done=hold_done,
            # R25-F1g: hold penuh sampai bar selesai (challengeTime server-driven,
            # biasanya 8-10s). JANGAN release paksa — release = bar drain + reset.
            max_hold=14.0,
            min_hold=0.5,
            tremor=0.0,  # kunci: diam total saat hold, jangan tremor
            cdp=cdp,
            frame_relative=frame_relative,
            cdp_x=cdp_x,
            cdp_y=cdp_y,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        print(f"{label} human_press_and_hold err: {message}")
        if "closed" in message.lower() or "targetclosed" in message.lower():
            print(f"{label} page/context 已关闭，跳过重按，交外层判定")
            held, passed = 0.0, False
        else:
            try:
                await page.mouse.down()
                await asyncio.sleep(random.uniform(11.0, 14.0))
                await page.mouse.up()
            except Exception:
                pass
            held, passed = 12.0, False

    try:
        page.remove_listener("response", _monitor)
    except Exception:
        pass

    # R25-F1b: 0 POST ke collector selama hold = event tidak sampai PX JS
    # (target kosong/iframe hidden) — tandai box_is_button=False supaya
    # caller bisa fallback ke target lain.
    if post_seen["n"] == 0:
        print(f"{label} ⚠ 0 POST ke hsprotect collector selama hold — target mungkin kosong")
        box_is_button = False

    print(f"{label} held {held:.1f}s{' (passed)' if passed else ''}")
    return {
        "held": held,
        "passed": passed,
        "box_is_button": box_is_button,
        "x": cx,
        "y": cy,
    }
