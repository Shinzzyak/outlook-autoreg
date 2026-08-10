# -*- coding: utf-8 -*-
"""Shumei (数美) CAPTCHA solver — slider + spatial_select + icon_select (CV).

Ported from wulu007/shumei-bypass (MIT) + taisuii/OpenCV_IconSelect approach.
Verified live 2026-08-11: register + fverify PASS (protocol 207).

Flow:
  register()   → GET /ca/v1/register (JSONP) → {rid, k, l, bg, fg, order?}
  fetch_img()  → GET castatic.fengkongcloud.cn{path}
  solve()      → CV solve → fverify() → POST /ca/v2/fverify (JSONP) → riskLevel

Encryption: DES-ECB, root key 'sshummei' (CryptoConfig.DES_ROOT_KEY).
Field-name map (te/fr/...) is protocol-specific — CryptoConfig.protocol=207.

Modes:
  slide          — cv2.matchTemplate + alpha mask (RGBA slice)
  spatial_select — shadow mask → morphology → distance transform → min contour
  icon_select    — red-pixel extraction + rotation matching (taisuii)
  auto_slide     — no solve needed (slideRatio=1)
"""
import base64
import json
import random
import time
from typing import Any, Optional, Tuple

from Crypto.Cipher import DES

try:
    import cv2
    import numpy as np
    HAVE_CV = True
except ImportError:
    HAVE_CV = False

try:
    import requests  # sync HTTP (api_server uses to_thread)
    HAVE_REQ = True
except ImportError:
    HAVE_REQ = False


class CryptoConfig:
    """Protocol-207 field-name map + DES keys (from wulu-shumei-bypass)."""
    DES_ROOT_KEY = "sshummei"
    protocol = 207
    appId = ("te", "ef4bef0b")
    channel = ("fr", "60c83964")
    lang = ("fq", "5992a161")
    safeParams = ("gr", "3954fb84")
    selectData = ("sp", "735c85df")
    mouseData = ("ox", "b06aad3b")
    duration = ("gt", "ed4576ba")
    trueWidth = ("sn", "cfa425d6")
    trueHeight = ("xb", "04a24a06")
    consoleCheck = ("eg", "3f6a0c6f")
    botDetection = ("xz", "cfcad8db")
    fixed = ("lo", "4f3dbadb")
    slideRatio = ("or", "f0e5bc10")


def _zpad(data: bytes, size: int = 8) -> bytes:
    return data + b"\x00" * (-len(data) % size)


def encrypt_field(key: str, data: bytes) -> str:
    raw = DES.new(key.encode(), DES.MODE_ECB).encrypt(_zpad(data, 8))
    return base64.b64encode(raw).decode()


def derive_key(register_detail: dict) -> str:
    """k = DES-ECB(sshummei)-decrypt of b64 k; take l bytes as key."""
    decoded = base64.b64decode(register_detail["k"])
    raw = DES.new(CryptoConfig.DES_ROOT_KEY.encode(), DES.MODE_ECB).decrypt(decoded)
    return raw[: register_detail["l"]].decode()


def _parse_jsonp(text: str) -> dict:
    t = text.strip()
    if t.endswith(")"):
        return json.loads(t[t.index("(") + 1 : -1])
    return json.loads(t)


def _json(v) -> str:
    return json.dumps(v, separators=(",", ":"))


def _get_time() -> int:
    return int(time.time() * 1000)


# ── trajectory (human-like) ──────────────────────────────────────────────
def generate_trace(distance: int, y_base: int = 0, duration: int = 0) -> list:
    """Ease-out √ curve + y jitter + timestamps (from wulu-shumei-bypass)."""
    duration = duration or random.randint(500, 1200)
    n = duration // 100 + 1
    points = [[0, y_base, 0]]
    y = y_base
    for i in range(1, n):
        eased = (i / (n - 1)) ** 0.5
        x = round(eased * distance)
        y += random.randint(-1, 1)
        t = i * 100 + random.randint(2, 8)
        points.append([x, round(y), t])
    points[-1] = [distance, points[-1][1], (n - 1) * 100 + random.randint(0, 5)]
    return points


def gen_times(start: int, end: int, n: int) -> list:
    if n <= 1:
        return [start]
    if end <= start:
        return [start] * n
    span = end - start
    offsets = sorted(random.random() for _ in range(n))
    offsets[-1] = 1.0
    return [start + round(span * o) for o in offsets]


# ── CV solvers ───────────────────────────────────────────────────────────
def solve_slide(bg_bytes: bytes, slice_bytes: bytes, scale: float = 0.5) -> float:
    """Slide: matchTemplate with alpha mask. Returns ratio (0..1) of bg width."""
    if not HAVE_CV:
        raise RuntimeError("opencv-python-headless required for slide mode")
    bg = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    sl = cv2.imdecode(np.frombuffer(slice_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
    if bg is None or sl is None:
        raise ValueError("Failed to decode slide images")
    if scale < 1:
        bg = cv2.resize(bg, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
        sl = cv2.resize(sl, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    sl_gray = cv2.cvtColor(sl[:, :, :3], cv2.COLOR_BGR2GRAY)
    mask = sl[:, :, 3]
    res = cv2.matchTemplate(bg, sl_gray, cv2.TM_CCOEFF_NORMED, mask=mask)
    max_loc = cv2.minMaxLoc(res)[-1]
    return max_loc[0] / bg.shape[1]


def solve_spatial_select(
    img_bytes: bytes, order: str = "", scale: float = 0.5
) -> Tuple[float, float]:
    """Spatial select: shadow mask → morphology → distance transform → min contour."""
    if not HAVE_CV:
        raise RuntimeError("opencv-python-headless required for spatial_select")
    img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Failed to decode spatial image")
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    r, g, b = cv2.split(img)
    avg = np.mean(img, axis=2, dtype=np.int16)
    mask = (
        (np.abs(np.int16(r) - avg) < 20)
        & (np.abs(np.int16(g) - avg) < 20)
        & (np.abs(np.int16(b) - avg) < 20)
    )
    result = np.full(img.shape[:2], 255, dtype=np.uint8)
    result[mask] = 0
    kernel = np.ones((3, 3), np.uint8)
    img2 = cv2.morphologyEx(result, cv2.MORPH_OPEN, kernel, iterations=2)
    dist = cv2.distanceTransform(img2, cv2.DIST_L2, 3)
    _, binary = cv2.threshold(dist, 0.1 * dist.max(), 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(np.uint8(binary), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("No contours found in spatial image")
    min_c = min(contours, key=lambda c: cv2.boundingRect(c)[2] * cv2.boundingRect(c)[3])
    x, y, w, h = cv2.boundingRect(min_c)
    return (x + w // 2) / img.shape[1], (y + h // 2) / img.shape[0]


def solve_icon_select(bg_bytes: bytes, icon_bytes: bytes) -> Tuple[float, float]:
    """Icon select: red-pixel extraction + rotation template match (taisuii)."""
    if not HAVE_CV:
        raise RuntimeError("opencv-python-headless required for icon_select")
    bg = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_COLOR)
    icon = cv2.imdecode(np.frombuffer(icon_bytes, np.uint8), cv2.IMREAD_COLOR)
    if bg is None or icon is None:
        raise ValueError("Failed to decode icon images")

    def _red_mask(img):
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        m1 = cv2.inRange(hsv, np.array([0, 120, 70]), np.array([10, 255, 255]))
        m2 = cv2.inRange(hsv, np.array([170, 120, 70]), np.array([180, 255, 255]))
        return cv2.bitwise_or(m1, m2)

    bg_red = _red_mask(bg)
    icon_red = _red_mask(icon)
    best = None
    for angle in range(0, 360, 6):  # rotate 6° step
        h, w = icon_red.shape
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        rot = cv2.warpAffine(icon_red, m, (w, h))
        res = cv2.matchTemplate(bg_red, rot, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if best is None or max_val > best[0]:
            best = (max_val, max_loc)
    _, (x, y) = best
    h, w = icon_red.shape
    return (x + w / 2) / bg.shape[1], (y + h / 2) / bg.shape[0]


# ── main client ──────────────────────────────────────────────────────────
class ShumeiSolver:
    BASE_URL = "https://captcha.fengkongcloud.com"
    STATIC_URL = "https://castatic.fengkongcloud.cn"

    def __init__(
        self,
        organization: str,
        app_id: str = "default",
        channel: str = "default",
        mode: str = "slide",
        version: str = "1.0.4",
        sdkver: str = "1.1.3",
        lang: str = "zh-cn",
        timeout: int = 15,
        proxy: Optional[str] = None,
    ):
        self.organization = organization
        self.app_id = app_id
        self.channel = channel
        self.mode = mode
        self.version = version
        self.sdkver = sdkver
        self.lang = lang
        self.timeout = timeout
        self.proxy = proxy
        self.session = requests.Session() if HAVE_REQ else None
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self._uuid = time.strftime("%Y%m%d%H%M%S") + "".join(
            random.choices("ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678", k=18)
        )

    def _get(self, path: str, params: dict) -> dict:
        params["callback"] = f"sm_{_get_time()}"
        resp = self.session.get(
            f"{self.BASE_URL}{path}", params=params, timeout=self.timeout
        )
        return _parse_jsonp(resp.text)

    def register(self) -> dict:
        params = {
            "organization": self.organization,
            "appId": self.app_id,
            "channel": self.channel,
            "lang": self.lang,
            "model": self.mode,
            "rversion": self.version,
            "captchaUuid": self._uuid,
            "sdkver": self.sdkver,
            "data": _json({}),
        }
        resp = self._get("/ca/v1/register", params)
        if isinstance(resp, dict) and resp.get("code") == 1100:
            return resp["detail"]
        raise RuntimeError(f"Register failed: {resp}")

    def fetch_img(self, path: str) -> bytes:
        resp = self.session.get(f"{self.STATIC_URL}{path}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.content

    def _encrypt(self, name: str, value) -> dict:
        fn, fk = getattr(CryptoConfig, name)
        data = value.encode() if isinstance(value, str) else _json(value).encode()
        return {fn: encrypt_field(fk, data)}

    def fverify(self, rr: dict, true_width: int = 300) -> dict:
        et = now = _get_time()
        if self.mode in ("select", "icon_select", "seq_select"):
            st = now - random.randint(3000, 8000)
        else:
            st = now - random.randint(1000, 1800)
        enc = self._encrypt
        body = {
            "organization": self.organization,
            "rid": rr["rid"],
            "captchaUuid": self._uuid,
            "rversion": self.version,
            "sdkver": self.sdkver,
            "protocol": CryptoConfig.protocol,
            "ostype": "web",
            "act.os": "web_pc",
            **enc("appId", self.app_id),
            **enc("channel", self.channel),
            **enc("lang", self.lang),
            **enc("safeParams", "10"),
            **enc("duration", et - st),
            **enc("trueWidth", true_width),
            **enc("trueHeight", true_width // 2),
            **enc("consoleCheck", 1),
            **enc("botDetection", 0),
            **enc("fixed", -1),
        }

        bg = self.fetch_img(rr["bg"]) if rr.get("bg") else None
        fp = self.fetch_img(rr["fg"]) if rr.get("fg") else None

        if self.mode == "slide":
            x = solve_slide(bg, fp)
            body |= enc("slideRatio", x)
            body |= enc("mouseData", generate_trace(int(x * true_width), 0, et - st))
        elif self.mode == "auto_slide":
            body |= enc("slideRatio", 1)
            body |= enc("mouseData", generate_trace(int(true_width * 0.867), 0, et - st))
        elif self.mode == "spatial_select":
            order = rr.get("order", [""])[0]
            point = solve_spatial_select(bg, order)
            data = [[*point, _get_time()]]
            body |= enc("selectData", data)
            body |= enc("mouseData", data)
        elif self.mode == "icon_select":
            pos = solve_icon_select(bg, fp)
            ts = gen_times(st, et, 1)
            data = [[*pos, ts[0]]]
            body |= enc("selectData", data)
            body |= enc("mouseData", data)
            body |= enc("duration", ts[-1] - st)
        else:
            raise NotImplementedError(f"unsupported mode: {self.mode}")

        return self._get("/ca/v2/fverify", body)

    def solve(self, retry: int = 3) -> dict:
        last = None
        for _ in range(retry):
            rr = self.register()
            res = self.fverify(rr)
            if res.get("code") != 1100:
                raise RuntimeError(f"Verify failed: {res}")
            if res.get("riskLevel") == "PASS":
                return res
            last = res
        raise RuntimeError(f"All attempts failed: {last}")


def solve_shumei(
    organization: str,
    mode: str = "slide",
    retry: int = 3,
    proxy: Optional[str] = None,
    timeout: int = 15,
) -> dict:
    """Synchronous entry point (for api_server to_thread). Returns fverify result."""
    s = ShumeiSolver(organization=organization, mode=mode, timeout=timeout, proxy=proxy)
    return s.solve(retry=retry)
