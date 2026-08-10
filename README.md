# Outlook Auto-Reg + Turnstile Solver

Auto-create Microsoft/Outlook accounts via stealth browser, with built-in CAPTCHA solving.

**Fork dari [tiantianGPU/reg-factory](https://github.com/tiantianGPU/reg-factory)** (educational license) — diadaptasi:
- **Playwright → patchright** (anti-detect fork, drop-in)
- **VPS mode** (`OUTLOOK_NO_BITBROWSER=1`): skip BitBrowser, headless only
- **Graph token**: HTTP fallback di VPS (tanpa browser)

## Kemampuan

| Kemampuan | Metode |
|---|---|
| **Auto-create Outlook** | patchright headless + stealth init-script, form fill, humanized |
| **PerimeterX hsprotect** (press-and-hold) | WindMouse + OU tremor, tahan 11-15s, retry 5-15x |
| **Arkose FunCaptcha** | CapSolver / EZ-Captcha API (`FunCaptchaTaskProxyLess`) |
| **Cloudflare Turnstile** (downstream) | `turnstile/` solver — patchright click checkbox + token extraction |
| **Graph refresh token** | OAuth auth-code flow pure HTTP (VPS) / browser (lokal) |

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/patchright install chromium
cp .env.example .env  # isi API keys (opsional)
```

## Usage

```bash
# 10 akun, 2 koncurrency, proxy file
OUTLOOK_NO_BITBROWSER=1 ./venv/bin/python register_outlook.py --count 10 --concurrency 2 --proxy-file proxies.txt

# 1 akun, no proxy (test)
OUTLOOK_NO_BITBROWSER=1 ./venv/bin/python register_outlook.py --count 1 --no-proxy --mode headless
```

Output: `outlook_accounts/` — `email----password----refresh_token----client_id`

### Argumen CLI

| Flag | Default | Keterangan |
|---|---|---|
| `--count, -n` | 10 | Jumlah akun |
| `--concurrency, -c` | 2 | Paralel (max 3-5 — MS rate limit keras) |
| `--proxy-file, -p` | — | 1 proxy/line (`user:pass@host:port`) |
| `--no-proxy` | false | Tanpa proxy |
| `--timeout, -t` | 300 | Timeout per akun |
| `--mode` | auto | `auto`/`protocol`/`headless`/`browser` |

## Rate limit Microsoft (dari pengalaman produksi reg-factory)

- **1-2 akun per IP** — setelah itu PerimeterX ERR_CONNECTION_CLOSED, WAJIB ganti proxy
- **Serial lebih aman** daripada concurrency tinggi
- **5s+ delay** antar attempt
- IP datacenter = langsung challenge berat; residential wajib untuk skala

## Turnstile solver (downstream)

```python
from turnstile.solve import solve_route
token, _ = await solve_route(page, url="https://target.site", sitekey="SITE_KEY")
```

## Struktur

```
register_outlook.py   # main: flow signup + CLI
reg_loop.py           # loop batch
graph_tokens.py       # OAuth refresh token extraction
agent_captcha.py      # CapSolver/EZ-Captcha/vision
config.py             # env config + API keys
common/outlook_press.py   # PerimeterX press-and-hold
common/human_mouse.py     # WindMouse + OU tremor
turnstile/            # Cloudflare Turnstile solver (dari toolkit)
outlook_accounts/     # output akun
```

## Disclaimer

Educational. Melanggar ToS Microsoft — akun bisa di-ban massal. Jangan untuk spam/phishing.
