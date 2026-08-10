# -*- coding: utf-8 -*-
"""SMS verification client for Outlook signup device challenge.

5sim primary (JSON, cheap), SMSPool fallback (non-VoIP US).
env: SMS5SIM_TOKEN, SMSPOOL_KEY

Usage:
    o = request_sms("microsoft", "usa")
    fill_phone_form(o["phone"])
    code = get_code(o, max_wait=180)   # poll sampai SMS masuk
    if not code: cancel_order(o)        # refund, jangan biarkan hang
"""
import os
import re
import time

import requests

BASE_5SIM = "https://5sim.net/v1"
BASE_SMSPOOL = "https://api.smspool.net"


def request_sms(service="microsoft", country="usa", operator="any", provider="5sim"):
    """Beli nomor virtual. Return dict: {provider, order_id, phone}."""
    if provider == "5sim":
        token = os.environ.get("SMS5SIM_TOKEN", "")
        if not token:
            raise RuntimeError("SMS5SIM_TOKEN not set")
        h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        r = requests.get(
            f"{BASE_5SIM}/user/buy/activation/{country}/{operator}/{service}",
            headers=h, timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f"5sim buy failed {r.status_code}: {r.text[:200]}")
        d = r.json()
        phone = d["phone"]
        # DD8-B: filter nomor rate rendah — rate1/rate3 = peluang SMS masuk
        # dalam 1/3 menit. Skip nomor rate1 < 40 (kemungkinan besar hang).
        rates = d.get("rate", {})
        try:
            if rates.get("rate1", 0) < 40 and rates.get("rate3", 0) < 60:
                # cancel & coba lagi sekali — kalau semua jelek, tetap pakai
                try:
                    requests.get(f"{BASE_5SIM}/user/cancel/{d['id']}", headers=h, timeout=10)
                except Exception:
                    pass
                raise RuntimeError(f"5sim low rate number ({rates})")
        except TypeError:
            pass  # rate bukan dict — format beda, lanjut
        return {"provider": "5sim", "order_id": str(d["id"]), "phone": phone}
    # ---- SMSPool fallback ----
    key = os.environ.get("SMSPOOL_KEY", "")
    if not key:
        raise RuntimeError("SMSPOOL_KEY not set")
    r = requests.post(
        f"{BASE_SMSPOOL}/purchase/sms",
        data={"key": key, "service": service, "country": country},
        timeout=30,
    )
    d = r.json()
    if d.get("status") not in (1, "1", "Success"):
        raise RuntimeError(f"smspool buy failed: {d}")
    return {"provider": "smspool", "order_id": str(d["orderid"]), "phone": d["number"]}


def get_code(order, max_wait=180, poll=5):
    """Poll SMS sampai dapat kode 6-8 digit. Return kode atau None (timeout)."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if order["provider"] == "5sim":
            r = requests.get(
                f"{BASE_5SIM}/user/check/{order['order_id']}",
                headers={"Authorization": "Bearer " + os.environ.get("SMS5SIM_TOKEN", "")},
                timeout=30,
            )
            d = r.json()
            if d.get("status") == "RECEIVED":
                for sms in d.get("sms", []):
                    m = re.search(r"(?<!\d)(\d{6,8})(?!\d)", sms.get("text", ""))
                    if m:
                        return m.group(1)
                # T-12: RECEIVED tapi belum ada kode → SMS kedua bisa nyusul,
                # jangan return None premature (order dibatalkan = refund padahal SMS nyusul)
                print("  5sim RECEIVED tanpa kode — lanjut poll...")
        else:  # smspool
            r = requests.post(
                f"{BASE_SMSPOOL}/sms/check",
                data={"key": os.environ.get("SMSPOOL_KEY", ""), "orderid": order["order_id"]},
                timeout=30,
            )
            d = r.json()
            if d.get("status") in (1, "1"):
                for sms in d.get("sms", []):
                    m = re.search(r"(?<!\d)(\d{6,8})(?!\d)", sms.get("message", ""))
                    if m:
                        return m.group(1)
        time.sleep(poll)
    return None


def cancel_order(order):
    """Refund kalau timeout / nomor gagal. Return True kalau sukses (T-13)."""
    try:
        if order["provider"] == "5sim":
            r = requests.get(
                f"{BASE_5SIM}/user/cancel/{order['order_id']}",
                headers={"Authorization": "Bearer " + os.environ.get("SMS5SIM_TOKEN", "")},
                timeout=30,
            )
        else:
            r = requests.post(
                f"{BASE_SMSPOOL}/sms/cancel",
                data={"key": os.environ.get("SMSPOOL_KEY", ""), "orderid": order["order_id"]},
                timeout=30,
            )
        return r.status_code == 200
    except Exception:
        return False
