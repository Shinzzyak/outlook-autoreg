# Outlook Auto-Reg + Turnstile Solver

Auto-create Microsoft/Outlook accounts via stealth browser, with built-in CAPTCHA solving.

**Fork dari [tiantianGPU/reg-factory](https://github.com/tiantianGPU/reg-factory)** — mesin
anti-bot yang terbukti produksi (PerimeterX press-and-hold + Arkose FunCaptcha + Graph
OAuth token), diadaptasi ke **Patchright** (anti-detect Playwright) supaya jalan di VPS
headless tanpa BitBrowser.

## Kemampuan

| Fitur | Detail |
|---|---|
| **Auto-register Outlook** | `register_outlook.py` — alur lengkap: form → captcha → profil → akun |
| **PerimeterX press-and-hold** | `common/outlook_press.py` — WindMouse + OU tremor, tahan 11–15s |
| **Arkose FunCaptcha solver** | CapSolver / EZ-Captcha + inject token (CE_READY/fc-token/fcCallback) |
| **Turnstile solver** | `turnstile/` — checkbox click + token extraction (untuk situs downstream) |
| **Device challenge** | recovery email temp (YYDS/GPTMail/MoeMail) + SMS (5sim/SMSPool, wire di `_bind_required_recovery_email`) |
| **Graph OAuth token** | `graph_tokens.py` — refresh_token via Thunderbird public client |
| **API server** | `api_server.py` — Quart: `/register`, `/solve/turnstile`, `/solve/arkose` |

## Quick start

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/patchright install chromium

# CLI — register 5 akun, serial, tanpa BitBrowser (VPS mode)
OUTLOOK_NO_BITBROWSER=1 ./venv/bin/python register_outlook.py --count 5 --no-proxy

# API server (auth opsional: API_TOKEN=secret; concurrency REG_MAX_CONCURRENCY=2)
API_TOKEN= OUTLOOK_NO_BITBROWSER=1 ./venv/bin/python api_server.py --port 8000
curl -X POST localhost:8000/register -H 'Authorization: Bearer '$API_TOKEN -H 'Content-Type: application/json' \
  -d '{"count":1,"proxy":"http://user:pass@host:port"}'   # → 202 + task_id, poll GET /register/<id>
curl -X POST localhost:8000/solve/turnstile -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","sitekey":"0x4AAAA..."}'
```

## Mode browser

| Mode | Kapan |
|---|---|
| `--mode headless` (default di VPS) | Tanpa BitBrowser, patchright stealth — disarankan VPS |
| `--mode protocol` | Pure-HTTP signup — **N/A**: signup.live.com sekarang SPA, HTTP-only tidak bisa (T-10) |
| `--mode browser` | BitBrowser (butuh GUI lokal + BitBrowser API) |

## Konfigurasi (`.env`)

```env
CAPSOLVER_API_KEY=     # Arkose FunCaptcha solver (wajib kalau MS tampilkan Arkose)
EZCAPTCHA_API_KEY=
SMS5SIM_TOKEN=         # device challenge phone — 5sim.net (murah, JSON)
SMSPOOL_KEY=           # fallback non-VoIP US
OUTLOOK_GRAPH_RECOVERY_PROVIDER=yyds,gptmail   # recovery email temp chain
OUTLOOK_NO_BITBROWSER=1                        # VPS mode
```

## Rate limit (penting!)

- **1–2 akun per IP** — pakai proxy pool (rotasi per akun)
- Serial > paralel — MS flag concurrency tinggi
- Pacing 5s+ antar aksi

## Catatan teknis

- Signup form Microsoft berubah (2026): input email `name=email` (bukan `MemberName`) —
  selector multi-fallback sudah handle
- `verify_registered_outlook` default-fail kalau modul check hilang (P1-3) — akun gagal
  tidak pernah diekspor sebagai sukses
- Proxy creds **tidak pernah di-hardcode** — selalu dari env (P1-4)

## Lisensi

Kode inti dari reg-factory (educational use). Lihat `LICENSE` upstream.
