# Platform configuration

This page covers project controls, artifact options, distributed roles, and
observability. Existing single-operator installations can continue without
enabling the project control plane or distributed roles.

## Configure projects, keys, quotas, and profiles

Set separate random `VIPERCAPTURE_ADMIN_TOKEN` and
`VIPERCAPTURE_CONTROL_SECRET` values of at least 32 bytes. The first
authenticates administrators; the stable second value encrypts profiles and
keys API-token fingerprints so administrator-token rotation does not corrupt
stored state. Every `/v1`,
`/take`, and `/compat` request then requires either that administrator token or
a project key. Create a project and its first key:

```bash
curl -H "Authorization: Bearer $VIPERCAPTURE_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"ci","requests_per_minute":120,"concurrency":4}' \
  http://127.0.0.1:8000/v1/admin/projects

curl -H "Authorization: Bearer $VIPERCAPTURE_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"github-actions"}' \
  http://127.0.0.1:8000/v1/admin/projects/PROJECT_ID/keys
```

Raw keys are returned once and only server-keyed digests are stored. Keys can be
restricted to any combination of the `render`, `jobs`, `schedules`, `profiles`,
and `baselines` scopes. Jobs, schedules,
profiles, and visual baselines are project-owned. Profile storage state is
AES-GCM encrypted at rest; profile IDs are unguessable and can be supplied as
`profile_id` on a render request.

Portable browser exports can be normalized through `POST /v1/profiles/import`.
This accepts Playwright storage state, common Chrome/Edge/Brave/Firefox cookie
JSON exports, Netscape `cookies.txt`, and a pasted Cookie header. Directly
reading local browser databases is deliberately out of scope: it is
platform-specific, often requires OS credential decryption, and would give the
service broader access than an explicit export.

Native routes use `Authorization: Bearer vcp_...`; Bearer authentication
failures include `WWW-Authenticate`. Query-string credentials are accepted
only by the ScreenshotOne-compatible `/take` route and can be disabled with
`VIPERCAPTURE_ALLOW_QUERY_AUTH=0`.

RPM events and active-render leases are transactionally recorded in the
control database. Processes using that same database therefore share project
limits; abandoned concurrency leases expire after 15 minutes.
Each project may retain at most 100 schedules and 512 MiB of encrypted schedule
payloads. Creation reserves quota before the durable schedule row is written;
updates resize that reservation and deletion releases it.

## Configure artifact options

`deterministic.enabled` fixes `Date`, `Math.random`, Web Crypto random values
and UUIDs, and the browser performance clock; it also waits for fonts and
combines with the existing animation stabilization. `slices` emits a ZIP
of bounded-height full-page sections. A diagnostic bundle can add a redacted
HAR 1.2 log (HTTP versions from Chromium CDP or Resource Timing, allowlisted
headers, mime types, and timings; query strings, matrix/path parameters,
cookies, credentials, and bodies omitted), Patchright trace, and WARC
files. Console capture is degraded because Patchright disables the Console
API. `certification.enabled` produces an
Ed25519-signed manifest when `VIPERCAPTURE_CERTIFICATION_SECRET` is set to at
least 32 bytes. Certification proves bundle integrity; it does not by itself
make a legal-admissibility claim.

## Configure distributed roles and observability

`VIPERCAPTURE_ROLE=api` starts queue providers without consumers;
`VIPERCAPTURE_ROLE=worker` consumes jobs and rejects public render traffic;
`all` remains the default. Split roles require a shared job-store factory,
shared artifact storage (S3/R2 or a factory), and `VIPERCAPTURE_JOB_SECRET`.
This makes accidental split deployment with local SQLite/files impossible.
The built-in SQLite control plane is restricted to `role=all`;
split deployments must enforce shared authentication and quotas at their
gateway. Schedules in split mode require a shared
`VIPERCAPTURE_SCHEDULE_STORE_FACTORY`; disable them explicitly otherwise.
Its paginated `list` operation must accept a `project_id` filter and apply it
inside indexed storage rather than scanning other tenants' rows.
The combined `all` role recovers its own interrupted claims on restart. Split
workers require an external job store with lease-based `recover_stale()` so a
replica can recover only expired claims and cannot steal live work.

`GET /metrics` exports Prometheus text. When the control plane is enabled it
requires the administrator Bearer token unless
`VIPERCAPTURE_METRICS_PUBLIC=1` is explicitly set. The supplied public gateway
continues to block `/metrics` while Prometheus scrapes the internal listener.
`GET /health` and `GET /ready` remain public; `/v1/admin/status` exposes
authenticated operator state. Set
`OTEL_EXPORTER_OTLP_ENDPOINT` to enable batched OpenTelemetry FastAPI traces.

## Use integrations

The repository includes an importable n8n workflow and a small Terraform
Docker module. Compatibility adapters reject unknown vendor options instead of
silently changing render behavior.

## Configure proxies, stealth, and CAPTCHA callbacks

Per-request proxies are enabled by default in self-host mode and disabled by
default in hosted mode. Set `VIPERCAPTURE_ALLOW_CUSTOM_PROXIES=0` to turn them
off or `=1` to opt in. The setting controls HTTP(S), SOCKS4, and SOCKS5 proxy
objects supplied under `network.proxy`; it does not weaken URL validation or
the recommended network egress firewall.

Patchright’s CDP stealth patches are always active (Runtime.enable
avoidance, Console API disable, automation-flag tweaks, closed shadow DOM,
init scripts via Routes). When `stealth` is true (the default) and the
process is using headless bundled Chromium, ViperCapture Stealth also
rewrites `HeadlessChrome` in the user-agent. That extra rewrite is skipped
for `VIPERCAPTURE_BROWSER_CHANNEL=chrome` or headed mode, matching
Patchright’s “do not inject a custom user-agent” guidance. Set
`stealth:false` to skip the UA rewrite for diagnosis. This is not
`playwright-stealth` and is not a CAPTCHA or Cloudflare exploit.

Prefer headed Chrome when the environment allows it **and** you are not in
Docker/GHCR. When `DISPLAY` is set on a workstation:

```bash
VIPERCAPTURE_BROWSER_CHANNEL=chrome
VIPERCAPTURE_HEADLESS=0
VIPERCAPTURE_PATCHRIGHT_PERSISTENT=1
```

`VIPERCAPTURE_PATCHRIGHT_SWEETSPOT=1` turns on that persistent headed Chrome
path (including `no_viewport`) unless Docker-style env already pins
`VIPERCAPTURE_HEADLESS=1` / `VIPERCAPTURE_BROWSER_CHANNEL=chromium`.
Docker/GHCR defaults remain `chromium` + `VIPERCAPTURE_HEADLESS=1` with
`launch()` + `new_context()`. Headless Chromium in slim images is weaker than
headed Chrome. Persistent mode uses a durable user-data directory under
`VIPERCAPTURE_DATA_DIR/patchright-profiles` (or
`VIPERCAPTURE_PATCHRIGHT_USER_DATA_DIR`) and skips custom user-agent injection.
Per-request viewport, user-agent, and proxy context options stay on the default
`launch()` + `new_context()` path; persistent mode is the Patchright sweet spot,
not a second full isolated-context renderer.

Cloudflare Turnstile: Stealth clicks a reachable checkbox via Patchright
frames/locators, waits for the interstitial to clear, and retries once. This
is not a Cloudflare bypass. Interactive challenges may still need a human or
a site-owner allowlist.

ViperCapture only auto-clicks that Turnstile widget; it does not ship solvers.
To let an operator connect an approved internal or third-party integration, set
`VIPERCAPTURE_CAPTCHA_HANDLER_FACTORY=package.module:create_handler`. The
factory is called once at startup and must return an async callable with this
contract:

```python
async def handler(page, challenge, solver_name, timeout_ms) -> bool:
    # Dispatch to the operator's integration. Return True only after the page
    # is ready for ViperCapture to re-check it.
    return False

def create_handler():
    return handler
```

Requests opt in with `"captcha":{"action":"external",
"solver":"operator-alias","timeout_ms":120000}`. The solver name is only a
non-secret routing label, not a provider credential. Keep handler credentials
in the operator-owned integration and out of render requests, target cookies,
headers, and profiles. ViperCapture invokes only the configured factory's
callable, enforces the timeout, re-runs detection, and fails if the challenge
remains. No provider integration, provider affiliation, credentials, token
injection, endorsement, solver, or challenge-bypass implementation ships with
the project.

Callers may instead complete an authorized access flow independently and make
a fresh render with short-lived, target-scoped session state as documented in
[site access](site-access.md). That caller-managed flow does not invoke the
operator handler. Neither flow relaxes same-origin header routing, hosted
target-domain checks for `network.cookies`, or network egress policy. Hosted
mode also keeps public-address and redirect validation. Imported profiles are
not target-filtered, and self-host mode permits internal targets, so operators
must use dedicated target-scoped profiles and enforce private and metadata-
network blocks at the egress layer.
