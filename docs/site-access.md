# Authorize ViperCapture Stealth through Cloudflare or another WAF

This guide is for owners and administrators authorizing captures of a site they
control. ViperCapture Stealth uses Patchright’s supported Chromium stealth
patches (Runtime.enable avoidance, Console API disable, automation flags,
closed shadow DOM, init-script injection via Routes). That is not a custom
Cloudflare exploit or CAPTCHA bypass. The service detects blocking challenges
and, for Cloudflare Turnstile, clicks a reachable checkbox through Patchright
frames/locators (including closed-shadow `cf-chl-widget-*` iframes that page
JavaScript cannot see), uses a human-like mouse path, waits for a managed
auto-pass, and retries with backoff. An embedded widget is not treated as
already passed. It does not call solver APIs, mint tokens, or evade another
site's access controls. Interactive Turnstile may still remain after an honest
click; treat that as a challenge, not a product failure you can “bypass.”
A public test-key widget (nowsecure.nl) can complete when the checkbox is
actually clicked. A closed-shadow managed challenge (for example
scrapingcourse) can complete when locators see the iframe. Neither is a
general Cloudflare bypass.

## Create an access rule

Create a dedicated preview hostname or path and require a random, revocable
request header. Match the header with the fixed outbound address of the
ViperCapture deployment. Skip only the security rule that blocks the request.
Keep logging and unrelated protections enabled.

For example, send an origin-scoped header with the render request:

```bash
curl http://127.0.0.1:8000/v1/render \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://www.example.com/capture-preview/report",
    "output": "png",
    "headers": {
      "X-ViperCapture-Key": "replace-with-a-long-random-value"
    }
  }' --output report.png
```

Caller-supplied headers are applied only to the exact origin of `url`. Redirects
and cross-origin assets do not receive the secret. Persistent profiles are a
better fit for short-lived login sessions; do not copy a person's long-lived
session into a request.

## Hand off from an external challenge tool

For a target you are authorized to test, a caller may complete the site's
access flow outside ViperCapture with a tool of its choice and then submit a
fresh render. Pass only short-lived, target-scoped state through an encrypted
profile, `network.cookies`, or an exact-origin header. Do not put the external
service's API key in target cookies, headers, profiles, or render payloads;
keep service credentials in the caller or operator integration.

This caller-managed handoff is separate from the optional operator handler
described in the [platform guide](platform.md). ViperCapture is not affiliated
with external providers and does not bundle, call, endorse, or configure one
for this workflow. It does not solve or bypass challenges. Same-origin header
routing remains in force, and hosted mode keeps its target-domain check for
`network.cookies` plus public-address and redirect validation. Imported profile
state is not target-filtered, so create a dedicated profile containing only the
authorized site's state. Self-hosters must enforce private and metadata-network
blocks with the deployment's egress policy.

## Configure Cloudflare

Create a WAF custom rule above the rule that blocks the renderer. Replace the
address, host, path, and value below:

```text
(
  ip.src eq 203.0.113.10 and
  http.host eq "www.example.com" and
  starts_with(http.request.uri.path, "/capture-preview/") and
  any(http.request.headers["x-vipercapture-key"][*] eq "replace-with-a-long-random-value")
)
```

Choose **Skip**, then select only the relevant managed rule, bot rule, or rate
limit. Do not globally disable the WAF. A broad IP allow rule is a last resort
because it cannot be limited by path and secret header. Cloudflare's current
documentation covers [Skip rules](https://developers.cloudflare.com/waf/custom-rules/skip/)
and [request-header expressions](https://developers.cloudflare.com/ruleset-engine/rules-language/fields/reference/http.request.headers/).

The same least-privilege shape applies to other CDNs and WAFs: renderer source
address, exact hostname, dedicated path, and a secret header. Check origin-side
rate limits and security middleware too.

## Troubleshooting

- For a 403 or Cloudflare error 1020, inspect the provider event and exact rule
  ID that matched.
- For a 429, exempt only this narrow integration from the relevant edge or
  origin limit.
- For `captcha_detected`, the renderer clicked a reachable Turnstile checkbox
  if one was present and the challenge still did not clear. For a site you
  administer, remove the challenge from the authorized rule or complete it as
  a human. `proceed_on_captcha: true` captures the challenge as displayed; it
  does not mint tokens or bypass Cloudflare.
- Interactive Turnstile without a clickable checkbox still cannot complete
  without a solver or a human. nowsecure.nl’s test-key widget can complete
  when the checkbox is clicked; many production widgets will not. That is an
  inherent interactive challenge, not a Stealth “bypass.”
- A 403 from the original navigation is ignored once the page shows a Turnstile
  token, success UI, or real-content bypass copy. Do not treat a stale 403 as
  proof the challenge is still up.
- For missing fonts or images, inspect the diagnostic bundle for blocked
  cross-origin assets and authorize an asset host only when you control it.

Verify the first capture in both edge and origin logs, confirm requests without
the secret remain protected, rotate the secret periodically, and remove the
exception when the integration is no longer needed.
