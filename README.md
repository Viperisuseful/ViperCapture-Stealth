<p align="center">
  <img src="static/vipercapture-mark.svg" width="112" height="112" alt="ViperCapture logo">
</p>

<h1 align="center">ViperCapture Stealth</h1>

<p align="center"><strong>ViperCapture Stealth 0.1.0-beta — first public beta.</strong></p>

This is the **first public beta** of [ViperCapture Stealth](https://github.com/Viperisuseful/ViperCapture-Stealth),
the stealth fork of [ViperCapture](https://github.com/Viperisuseful/ViperCapture)
(OSS 1.0.3). It keeps the same `/v1/render` JSON contract and logo assets, but
replaces Playwright with [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python),
a Chromium-only Playwright drop-in that patches CDP leaks used by Cloudflare
and other WAFs.

- Upstream OSS: https://github.com/Viperisuseful/ViperCapture (1.0.3 contract)
- This fork: https://github.com/Viperisuseful/ViperCapture-Stealth
- Product version: **0.1.0-beta** (tag `v0.1.0-beta`)
- Published image: `ghcr.io/viperisuseful/vipercapture-stealth:0.1.0-beta`

## What’s in 0.1.0-beta

- **Patchright swap** — Playwright is replaced by Patchright Chromium
- **Persistent / sweet-spot** — headed persistent Chrome when a workstation
  display is available; Docker/GHCR stay headless bundled Chromium
- **Turnstile click path** — complete-when-possible via locators/frames (#5)
  plus the `stop_when` bind fix (#6)
- **Chromium-only** — Firefox and WebKit requests are rejected

This is a prerelease. It is **not** a Cloudflare bypass. See
[Stealth mode / Patchright](#stealth-mode--patchright) for install, env knobs,
Turnstile complete-when-possible, and the headed-Chrome sweet-spot quickstart.

ViperCapture Stealth is an MIT-licensed browser renderer for infrastructure
you control. Send a URL, HTML, or Markdown and receive screenshots, PDFs,
AVIF images, WebM/MP4/GIF video, hydrated HTML, Markdown, or structured
metadata through a JSON API.

## Stealth mode / Patchright

This section is the how-to for Stealth / Patchright / Turnstile / Cloudflare
**challenge handling**. It is **not** a universal Cloudflare bypass. Prefer
the language “complete-when-possible”: Stealth clicks a reachable Turnstile
checkbox through Patchright’s supported surface and waits for a real pass
marker. Many production widgets and interactive hard challenges will still
remain. For sites you administer, keep using the least-privilege
[Cloudflare/WAF authorization guide](docs/site-access.md).

### Install Patchright browsers

Install **either** Google Chrome (sweet-spot path) **or** bundled Chromium
(Docker/CI/headless path):

```bash
python -m patchright install chrome      # headed persistent Chrome
python -m patchright install chromium    # bundled Chromium (Docker/GHCR/CI)
```

On Linux, add `--with-deps` if system libraries are missing
(`python -m patchright install --with-deps chrome`). Omit `--with-deps` on
macOS and Windows.

`python launch.py` already runs that install after creating `.venv` and
installing `requirements.txt`. The target is `chrome` when
`VIPERCAPTURE_BROWSER_CHANNEL=chrome` **or** when the DISPLAY sweet spot is
active; otherwise it installs `chromium`. Subsequent launches skip the
browser install when the Patchright version and target have not changed.

### Headed Chrome sweet spot vs headless Docker

Patchright’s strongest **supported** setup is **headed Google Chrome** with a
persistent profile, native window size, and no custom user-agent
(`launch_persistent_context`, `channel="chrome"`, `headless=False`,
`no_viewport=True`). That is the path that cleared public test widgets in
A/B (scrapingcourse + nowsecure). Headless Chromium in slim images is weaker
and more detectable.

The GHCR/Docker default still uses **headless bundled Chromium** and
`launch()` + `new_context()` so `ghcr.io/viperisuseful/vipercapture-stealth`
is usable without a display or system Chrome. CI stays on that path. Do not
force headed Chrome inside the published image.

When `DISPLAY` or `WAYLAND_DISPLAY` is set on a workstation **outside
Docker**, sweet-spot defaults apply automatically (persistent + `chrome` +
headed + `no_viewport`). Docker is detected via `/.dockerenv` or
`VIPERCAPTURE_IN_DOCKER=1`; container Xvfb `DISPLAY` does **not** turn on
the sweet spot. Explicit `VIPERCAPTURE_PATCHRIGHT_SWEETSPOT=0` or `=1` always
wins. The image pins `VIPERCAPTURE_HEADLESS=1`,
`VIPERCAPTURE_BROWSER_CHANNEL=chromium`,
`VIPERCAPTURE_PATCHRIGHT_SWEETSPOT=0`, and
`VIPERCAPTURE_PATCHRIGHT_PERSISTENT=0`.

This fork is **Chromium-only**. Requests with `engine: "firefox"` or
`"webkit"` are rejected. Firefox and WebKit are not installed.

### Environment knobs

Defaults below match `vipercapture/browser_launch.py` and the Dockerfile
pins at this tip. Empty / unset uses the Default column.

| Knob | Default | Notes |
| --- | --- | --- |
| `VIPERCAPTURE_PATCHRIGHT_SWEETSPOT` | auto when `DISPLAY` or `WAYLAND_DISPLAY` is set **and** not Docker; else `0` | Composite: persistent + headed Chrome + `no_viewport`. Explicit `=0` or `=1` wins. Docker pins of `HEADLESS` / `CHANNEL` still win over those two defaults. |
| `VIPERCAPTURE_PATCHRIGHT_PERSISTENT` | `0` (on when sweet spot is on) | `launch_persistent_context` with a durable `user_data_dir` under `VIPERCAPTURE_DATA_DIR/patchright-profiles` (or `VIPERCAPTURE_PATCHRIGHT_USER_DATA_DIR`) |
| `VIPERCAPTURE_BROWSER_CHANNEL` | `chromium` (defaults to `chrome` when sweet spot is on **and** this knob is unset) | `chromium` or `chrome` only. Install the matching browser first. |
| `VIPERCAPTURE_HEADLESS` | `1` (defaults to `0` when sweet spot is on **and** this knob is unset) | Set `0` for headed mode when a display is available. Do not force headed in Docker/GHCR. |
| `VIPERCAPTURE_PATCHRIGHT_NO_VIEWPORT` | off unless persistent headed Chrome | Use the real window instead of a fixed viewport. |
| `VIPERCAPTURE_TURNSTILE_CLICK` | `1` | Click a reachable Turnstile checkbox; set `0` for detect-only. |
| `VIPERCAPTURE_TURNSTILE_TIMEOUT_MS` | `30000` | Budget for auto-pass wait, checkbox click, and backoff retries (clamped 1000–120000). |
| `VIPERCAPTURE_SWIFTSHADER` | `0` | Software WebGL on GPU-less Xvfb (`--use-gl=angle --use-angle=swiftshader`). Does **not** spoof GPU fingerprints. |
| `DISPLAY` / `WAYLAND_DISPLAY` | unset | Workstation display. Triggers sweet-spot auto **only** outside Docker. |

Related: `VIPERCAPTURE_IN_DOCKER=1` forces the “in Docker” branch of sweet-spot
auto. Persistent profiles skip custom user-agent injection. Per-request
viewport, user-agent, and proxy context options stay on the default
`launch()` + `new_context()` path.

### Turnstile complete-when-possible

After PRs [#5](https://github.com/Viperisuseful/ViperCapture-Stealth/pull/5)
and [#6](https://github.com/Viperisuseful/ViperCapture-Stealth/pull/6),
Stealth tries to **complete** a Cloudflare Turnstile widget when Patchright
can actually reach it. Plain-language behavior:

1. **Locator click, not a blind `page.evaluate`.** Closed-shadow Turnstile
   (typical `cf-chl-widget-*` iframes) is invisible to in-page JavaScript.
   Stealth uses Patchright frames and locators (`get_by_role("checkbox")`,
   frame locators, then host widgets) and a human-like mouse path. It does
   not poke closed shadow through `page.evaluate`.
2. **Closed-shadow / `cf-chl-widget-*` frames still count.** If evaluate
   reports `provider=unknown` but a native Turnstile frame exists, the click
   path still runs (this is what unblocked scrapingcourse-style managed
   pages).
3. **No false auto-pass on mere `embedded_widget`.** A visible checkbox is
   not treated as already passed. Clear requires the widget to be gone, or a
   token / success UI / real-content bypass marker (`cf-turnstile-response`,
   “you bypassed”, “you have been verified”, `#challenge-success`).
4. **`stop_when` must bind `page`.** The auto-pass wait calls `stop_when()`
   with no arguments. #6 binds
   `stop_when=lambda: _checkbox_target_available(page)` so that probe cannot
   `TypeError` on a live challenge page.
5. **Ignore a stale navigation 403** once a token, success UI, or
   real-content marker is present. Post-click waits also drop the original
   403. A leftover 403 is not proof the challenge is still up.
6. **Retries stay honest.** After a click, Stealth waits, then retries the
   checkbox with backoff (about 400 / 800 / 1600 ms, up to three attempts)
   inside `VIPERCAPTURE_TURNSTILE_TIMEOUT_MS`. It never mints tokens.

**When it works:** public test widgets and clickable checkboxes that A/B
cleared on scrapingcourse and nowsecure with **persistent headed Chrome**.
Those are complete-when-possible results, not a product guarantee.

**When it will not:** interactive hard challenges and many production
widgets. Headless Docker/GHCR is weaker and less reliable than headed
Chrome, but the click path is not gated off: if a locator reaches the
checkbox and a token or success marker appears, the challenge can still
clear. A checkbox the locator cannot see still needs a human or a
site-owner allowlist. Detected
challenges that do not clear return `captcha_detected` unless you opt into
`proceed_on_captcha` (capture as shown) or an operator-approved external
handler. See [site access](docs/site-access.md).

### What Patchright actually covers

Stealth comes from **Patchright’s supported surface**, not custom Cloudflare
exploits:

- Avoids Playwright’s `Runtime.enable` leak by evaluating JavaScript in
  isolated execution contexts
- Disables the Console API to avoid `Console.enable` leaks (diagnostic
  `console.json` may be empty)
- Tweaks automation flags (`--disable-blink-features=AutomationControlled`,
  removes `--enable-automation` and other detectable defaults)
- Interacts with closed shadow DOM through normal locators
- Injects init scripts via Playwright Routes instead of `Runtime.enable`

This fork does **not** ship CAPTCHA solvers, token farms, WebGL fingerprint
spoofing, `playwright-stealth` stacked on Patchright, or a “bypass
everything” Cloudflare exploit. Do not market it as one.

### Sweet-spot quickstart

This is the copy-paste path that cleared scrapingcourse + nowsecure in A/B:
persistent headed Chrome on a real display (or Xvfb + SwiftShader). Use a
URL **you own or are authorized to test**. Docker/GHCR defaults are weaker
and are not this path.

```bash
git clone https://github.com/Viperisuseful/ViperCapture-Stealth.git
cd ViperCapture-Stealth

# Composite sweet spot, or the equivalent explicit knobs:
export VIPERCAPTURE_PATCHRIGHT_SWEETSPOT=1
# Equivalent:
#   export VIPERCAPTURE_PATCHRIGHT_PERSISTENT=1
#   export VIPERCAPTURE_BROWSER_CHANNEL=chrome
#   export VIPERCAPTURE_HEADLESS=0
#   export VIPERCAPTURE_PATCHRIGHT_NO_VIEWPORT=1
#   export VIPERCAPTURE_TURNSTILE_CLICK=1
# On GPU-less Xvfb (WebGL “no context”):
#   export VIPERCAPTURE_SWIFTSHADER=1

python launch.py
# launch.py creates .venv, installs requirements, then runs
#   python -m patchright install chrome
```

If the environment already exists, install Chrome yourself:

```bash
.venv/bin/python -m patchright install chrome
# Linux, if system libraries are missing:
# .venv/bin/python -m patchright install --with-deps chrome
```

Then render a page you are authorized to capture:

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/render \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://your-authorized-test.example/",
    "output": "png",
    "full_page": false,
    "viewport": {"width": 1280, "height": 720}
  }' --output capture.png
```

On a workstation with `DISPLAY` / `WAYLAND_DISPLAY` already set, you can
omit `VIPERCAPTURE_PATCHRIGHT_SWEETSPOT=1`; launch.py will install Chrome
and Stealth will take the sweet-spot defaults. Set
`VIPERCAPTURE_PATCHRIGHT_SWEETSPOT=0` to keep headless bundled Chromium.

## Features

- Patchright Chromium rendering (Firefox/WebKit are not supported in this fork)
- PNG, JPEG, WebP, AVIF, PDF with explicit structure-tag control, HTML,
  Markdown, metadata with structured CSS-selector extraction, and WebM/MP4/GIF
  output
- URL, raw HTML, and Markdown input; full-page, viewport, element, clip, and
  multi-viewport ZIP captures
- One-page-load artifact bundles with the primary image, HTML, Markdown,
  metadata, MHTML, and up to five named thumbnails
- Typed click, hover, fill, select, key, scroll, wait, hide, and opt-in
  JavaScript actions
- Selector-state and image-readiness waits, target JavaScript control,
  assertions, custom CSS, devices, locale/timezone,
  geolocation, cookies, user agent, proxy, resource blocking, and cleanup
- Patchright CDP stealth, operator-controlled residential/datacenter
  proxies, and structured detection for common CAPTCHA and bot interstitials
- Ad, tracker, chat, newsletter, and consent-banner cleanup backed by the
  vendored, license-preserved AutoConsent rule set
- Cleanup and deterministic controls in the browser UI
- Explicit screen or print CSS media emulation applied before page load
- Timed, full-page GIF, WebM, and MP4 capture with higher-quality encoding,
  transparent padding where supported, and optional GPU acceleration with a
  safe software fallback
- Default local limits: 500 megapixels, 16,384-pixel viewports, and 100,000-pixel
  full-page height, all configurable for remote hosting
- Durable encrypted async jobs, idempotency, retries, polling, cancellation,
  bulk submission, cron schedules, and signed webhook callbacks
- Private local result storage or built-in S3-compatible storage for AWS S3,
  Cloudflare R2, MinIO, Backblaze B2, and compatible providers
- Expiring HMAC-signed render URLs and a 24-hour exact-request image cache
- Visual regression ZIPs with pixel counts, pass/fail thresholds, bounds, and
  highlighted changes
- Diagnostic ZIPs with optional console/network data and redacted HAR
  (HTTP versions, safe headers, mime types, timings),
  redacted Patchright trace, and WARC; Ed25519-certified artifact bundles.
  Console capture is degraded because Patchright disables the Console API.
- Deterministic capture controls, sectioned slice ZIPs, project-owned visual
  baselines, and reproducible comparison reports
- Optional projects, hashed API keys, quotas, resource ownership, encrypted
  persistent profiles, portable browser-session imports, audit logs,
  Prometheus metrics, and OTLP tracing
- Separate API/worker roles for horizontally scaled provider-backed queues,
  plus ScreenshotOne and Urlbox compatibility adapters
- Bounded concurrency, output/pixel/deadline limits, client-disconnect
  cancellation, consistent error envelopes, and hosted-mode SSRF defenses
- Browser UI, Docker Compose, an n8n workflow, a Terraform module, and a
  hardened public-API deployment example

## Before you begin

Direct installation requires Python 3.11 or newer and a full FFmpeg build on
`PATH`. Video output needs the `libvpx`, `libvpx-vp9`, and `libx264` encoders;
GPU video also needs the matching hardware encoder and driver. Use your OS
package manager or the [FFmpeg download page](https://ffmpeg.org/download.html).
Run `ffmpeg -encoders` to confirm the encoders are available. The Docker image
already includes FFmpeg.

[uv](https://docs.astral.sh/uv/) is the preferred way to install Python
dependencies. It is optional: `python launch.py` still works with pip when uv
is not on `PATH`.

## Install locally

Install uv first if you can
([installation guide](https://docs.astral.sh/uv/getting-started/installation/)):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows, use `irm https://astral.sh/uv/install.ps1 | iex` in PowerShell.
Then run:

```bash
git clone https://github.com/Viperisuseful/ViperCapture-Stealth.git
cd ViperCapture-Stealth
python launch.py
```

The launcher prefers uv when it is on `PATH`: it creates `.venv` and installs
from `requirements.txt`. If uv is missing, it falls back to the standard
library `venv` module and pip. Set `VIPERCAPTURE_USE_UV=0` to force that pip
path even when uv is installed. The launcher then installs Patchright
`chrome` or `chromium` (see [Stealth mode / Patchright](#stealth-mode--patchright)),
starts the API, and opens `http://127.0.0.1:8000`. For the headed Chrome
sweet spot, set `VIPERCAPTURE_PATCHRIGHT_SWEETSPOT=1` (or
`VIPERCAPTURE_BROWSER_CHANNEL=chrome` and `VIPERCAPTURE_HEADLESS=0`) before
`python launch.py`.

To use Docker instead, run:

```bash
docker compose up --build
```

Stable container images are also published to GitHub Container Registry:

```bash
docker pull ghcr.io/viperisuseful/vipercapture-stealth:0.1.0-beta
```

The image defaults to headless bundled Chromium. That is weaker than headed
Chrome but keeps GHCR usable on servers without a display.

By default, Compose binds only to loopback. It keeps durable queue state,
encryption keys, schedules, cache entries, and local artifacts in a named
volume. Read [self-hosting](docs/self-hosting.md) before exposing the service to
a network. Set separate `VIPERCAPTURE_ADMIN_TOKEN` and
`VIPERCAPTURE_CONTROL_SECRET` values to enable the built-in project control
plane before exposing API routes to multiple tenants.

## Send a render request

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/render \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://example.com",
    "output": "png",
    "full_page": false,
    "viewport": {"width": 1280, "height": 720},
    "actions": [{"type": "hide", "selector": ".newsletter"}],
    "cleanup": {"block_ads": true, "consent_mode": "reject"},
    "assertions": {"content_includes": ["Example Domain"]}
  }' --output example.png
```

A synchronous request returns the artifact directly. The
`X-ViperCapture-*` headers include the request ID, queue and render timing,
dimensions, navigation status, and cache outcome.

For work that must survive a dropped connection, send the same render object to
`POST /v1/jobs`. Poll
`GET /v1/jobs/{id}`, download `GET /v1/jobs/{id}/result`, or receive a signed
callback by setting `delivery.webhook_url`. Related orchestration endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/jobs/bulk` | Best-effort submission of up to 100 independently idempotent jobs |
| `POST/GET/PATCH/DELETE /v1/schedules` | Encrypted five-field cron schedules with IANA time zones |
| `POST /v1/signed-url` | Mint an expiring HMAC render link |
| `GET /v1/render/signed` | Render through a verified signed link |
| `POST /v1/diff` | Compare two images and download a deterministic report ZIP |
| `PUT/GET/POST /v1/baselines` | Store project baselines and compare review bundles |
| `POST/DELETE /v1/profiles` | Manage encrypted Playwright storage-state profiles |
| `POST /v1/profiles/import` | Import pasted Cookie headers or browser-exported session files |
| `GET /take` | ScreenshotOne-compatible common-options adapter |
| `POST /compat/urlbox/v1/render/{sync,async}` | Urlbox-compatible adapters |

See the [API and workflows guide](docs/api.md), [async provider guide](docs/async-jobs.md),
[platform/operator guide](docs/platform.md),
[public API deployment](deploy/public-api), and
[migration guide](docs/migration-screenshotone-urlbox.md). If a site you
administer challenges the renderer, use the least-privilege
[Cloudflare/WAF authorization guide](docs/site-access.md).

## Use proxies, sessions, and CAPTCHA hooks

Self-hosted renders can route an isolated browser context through an HTTP,
HTTPS, SOCKS4, or SOCKS5 proxy. Keep credentials in separate fields instead of
embedding them in the proxy URL:

```json
{
  "url": "https://example.com",
  "network": {
    "proxy": {
      "server": "socks5://proxy.example:1080",
      "username": "account-zone-residential",
      "password": "secret"
    }
  }
}
```

With the project control plane enabled, `POST /v1/profiles/import` normalizes
Playwright storage state, Cookie-Editor JSON, Netscape `cookies.txt`, or a
pasted Cookie header into an encrypted profile. Pass its returned `id` as
`profile_id` on later renders. Imports preserve local storage and partitioned
cookies where the export format supports them.

ViperCapture Stealth detects common blocking CAPTCHA and bot interstitials.
Cloudflare Turnstile is **complete-when-possible** (PRs #5 and #6): locator
click through closed-shadow / `cf-chl-widget-*` frames, no false auto-pass on
mere widget presence, stale 403 ignored after a token/success/real-content
marker. Full behavior and limits are in
[Stealth mode / Patchright](#stealth-mode--patchright). It does **not** call
solver APIs, mint tokens, or implement a Cloudflare bypass. Interactive
Turnstile can still remain after an honest click. The default `captcha.action`
is `error`; use `capture` to render the uncleared challenge as-is. Operators
may configure their own approved async handler with
`VIPERCAPTURE_CAPTCHA_HANDLER_FACTORY` and opt in per request with
`captcha.action: "external"`. See the [API guide](docs/api.md) for the handler
contract and timeout behavior. Alternatively, an authorized caller can use an
external tool independently, then start a fresh render with short-lived,
target-scoped session state. This fork ships no provider integration,
credentials, endorsement, solver, or bypass service.

## Configure storage and webhooks

Set `VIPERCAPTURE_S3_BUCKET` to store job results through the built-in S3
adapter instead of local files. It uses standard AWS credential resolution.
R2 and MinIO also need an endpoint URL and, where appropriate, path-style
addressing. The `docker-compose.s3.yml` overlay provides a complete local MinIO
example:

```bash
export MINIO_ROOT_PASSWORD='replace-with-at-least-a-long-random-secret'
docker compose -f docker-compose.yml -f docker-compose.s3.yml up --build
```

Set `VIPERCAPTURE_WEBHOOK_SECRET` to enable callbacks. Each callback contains
canonical JSON signed with HMAC-SHA256 in
`X-ViperCapture-Webhook-Signature`. Timestamp and event-ID headers support
verification and deduplication. Private callback targets are rejected unless
the operator explicitly opts in. Public DNS results stay pinned for the life
of the callback connection to prevent rebinding, and the encrypted delivery
outbox survives process restarts.

## Secure the service

Keep the service on loopback, enable `VIPERCAPTURE_ADMIN_TOKEN`, or put every
route behind the same authenticated, rate-limited reverse proxy. When the
control plane is disabled, job and schedule UUIDs identify resources but do
not provide access control. Hosted mode rejects targets and redirects that
resolve to private addresses during validation, along with unsafe subresources,
cross-origin credential headers, proxy use, and cross-site cookies. Browser DNS
can change after validation, so deployments also need host or container egress
rules to block rebinding. Self-host mode allows internal pages and
proxies; isolate Chromium with that boundary in mind.

JavaScript actions are disabled unless `VIPERCAPTURE_ALLOW_SCRIPTS=1`. Render
inputs can contain credentials and private page data. Async and scheduled inputs
are AES-GCM encrypted and erased at terminal job states, but diagnostic console
output and browser video can still record sensitive page content. Treat those
artifacts accordingly.

## Verify an installation

```bash
uv venv
uv pip install -r requirements.txt
.venv/bin/python -m patchright install --with-deps chromium
npm ci --prefix frontend && npm run lint --prefix frontend && npm run build --prefix frontend
.venv/bin/python scripts/smoke.py
```

Without uv, use `python -m venv .venv` and
`.venv/bin/python -m pip install -r requirements.txt` instead, then the same
Patchright, frontend, and smoke commands. On Windows, use
`.venv\Scripts\python -m pip install -r requirements.txt` and
`.venv\Scripts\python` for Patchright and smoke, and omit `--with-deps` from
the Patchright command. On macOS, omit `--with-deps`.
The smoke command starts a temporary local server and verifies Chromium,
OpenAPI, health, output dimensions, and that Firefox/WebKit are rejected. Pass
`--base-url http://host:port` to check an existing deployment instead. For an
authenticated deployment, set `VIPERCAPTURE_SMOKE_TOKEN` to an API or
administrator token.

For headed Chrome on a workstation with a display, use the
[sweet-spot quickstart](#sweet-spot-quickstart) (`VIPERCAPTURE_PATCHRIGHT_SWEETSPOT=1`
or the equivalent explicit knobs) and:

```bash
.venv/bin/python -m patchright install chrome
```

On GPU-less Xvfb, headed Chrome often reports WebGL “no context”. Set
`VIPERCAPTURE_SWIFTSHADER=1` for software WebGL, or `VIPERCAPTURE_GPU_MODE=auto`
when a real GPU is present. That enables a GL implementation; it does not
spoof a GPU fingerprint. Docker/GHCR should keep `VIPERCAPTURE_HEADLESS=1`.

## License

[MIT](LICENSE). AutoConsent assets under `vendor/autoconsent` retain their
upstream license and attribution. Patchright is Apache-2.0 and remains a
separate dependency.
