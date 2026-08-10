# -*- coding: utf-8 -*-
"""Shared Outlook PerimeterX press-and-hold behavior.

Registration and recovery must use this module together. Keeping target
selection and the physical press sequence in one place prevents the two flows
from slowly diverging as Microsoft's challenge markup changes.
"""

from __future__ import annotations

import asyncio
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
            'iframe[src*="hsprotect.net"], '
            'iframe[src*="arkose"], '
            'iframe[src*="funcaptcha"]'
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
    (inner iframe display:none). Cari tombol di dalam child frame dulu."""
    # 1) child frames: cari tombol press-and-hold di dalam iframe hsprotect
    for frame in page.frames:
        if "hsprotect.net" not in (frame.url or "") or "challenge" not in (frame.url or ""):
            continue
        for sel in ('button[role="button"]', "#px-captcha", 'button:has-text("Press and hold")'):
            try:
                el = frame.locator(sel).first
                if await el.count() > 0:
                    box = await el.bounding_box()
                    if box and box["width"] > 30 and box["height"] > 8:
                        return box, True
            except Exception:
                pass

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
                        return box, True
        except Exception:
            pass
        try:
            box = await frame.locator("#px-captcha").first.bounding_box()
            if box and box["width"] > 30 and box["height"] > 8:
                # verifikasi inner iframe visible
                inner = frame.locator("#px-captcha iframe").first
                if await inner.count() > 0:
                    disp = await inner.evaluate("el => getComputedStyle(el).display")
                    if disp != "none":
                        return box, True
        except Exception:
            pass

    # 3) main frame: button langsung (accessible challenge)
    try:
        for sel in (
            'button:has-text("Press and hold")',
            'button:has-text("Appuyer et maintenir")',
            'button:has-text("按住")',
            'button:has-text("长按")',
        ):
            el = page.locator(sel).first
            if await el.count() > 0:
                box = await el.bounding_box()
                if box and box["width"] > 30 and box["height"] > 8:
                    return box, True
    except Exception:
        pass

    # 4) fallback lama: iframe hsprotect bounding box
    try:
        frames = page.locator('iframe[src*="hsprotect.net"]')
        for index in range(await frames.count()):
            box = await frames.nth(index).bounding_box()
            if box and box["width"] > 50 and box["height"] > 30:
                return box, False
    except Exception:
        pass
    return None, False


async def press_and_hold(page, *, label="", press_number=1):
    """Run one registration-style hold attempt, or return None without a target."""
    target_box, box_is_button = await find_hold_target(page)
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

    async def hold_done():
        return not await captcha_visible(page)

    # R25-F1b: monitor POST ke collector hsprotect — kalau 0 POST dalam 3s,
    # target salah (event tidak sampai) → abort cepat, jangan hold buta.
    post_seen = {"n": 0}
    original_send = None

    async def _monitor(resp):
        try:
            if "hsprotect.net/api" in (resp.url or "") and resp.request.method == "POST":
                post_seen["n"] += 1
        except Exception:
            pass

    try:
        page.on("response", _monitor)
    except Exception:
        pass

    try:
        held, passed = await human_mouse.human_press_and_hold(
            page,
            cx,
            cy,
            is_done=hold_done,
            max_hold=random.uniform(11.0, 15.0),
            min_hold=1.5,
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
