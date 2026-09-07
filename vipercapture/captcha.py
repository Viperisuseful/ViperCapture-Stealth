"""Challenge detection, Turnstile checkbox clicks, and an operator CAPTCHA hook.

Cloudflare Turnstile handling clicks the visible checkbox through Patchright
frames/locators and waits for the interstitial to clear. It does not call
solver APIs, mint tokens, or use undocumented Cloudflare endpoints.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import time
from collections.abc import Awaitable, Callable
from typing import Any, AsyncContextManager

from .browser_launch import turnstile_click_enabled, turnstile_timeout_ms
from .render_errors import RenderError

CaptchaHandler = Callable[[Any, dict[str, object], str | None, int], Awaitable[bool]]

DETECT_CHALLENGE_SCRIPT = r"""({ status }) => {
    const visible = (element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" &&
            Number(style.opacity) > 0 && rect.width > 0 && rect.height > 0;
    };
    const obstruction = (element) => {
        const rect = element.getBoundingClientRect();
        const viewportArea = Math.max(1, innerWidth * innerHeight);
        const area = Math.max(0, rect.width) * Math.max(0, rect.height);
        const coversCenter = rect.left <= innerWidth / 2 && rect.right >= innerWidth / 2 &&
            rect.top <= innerHeight / 2 && rect.bottom >= innerHeight / 2;
        const ratio = area / viewportArea;
        return ratio >= 0.25 || (coversCenter && ratio >= 0.10);
    };
    const roots = [document];
    for (let index = 0; index < roots.length && roots.length < 256; index += 1) {
        for (const element of roots[index].querySelectorAll("*")) {
            if (element.shadowRoot && roots.length < 256) roots.push(element.shadowRoot);
        }
    }
    const query = (selectors) => selectors.flatMap((selector) =>
        roots.flatMap((root) => [...root.querySelectorAll(selector)]).filter(visible));
    const providers = {
        cloudflare: {
            widgets: [".cf-turnstile", "iframe[src*='challenges.cloudflare.com']"],
            blocking: ["#challenge-stage", "#challenge-running", "#challenge-form",
                "iframe[src*='/cdn-cgi/challenge-platform/']"]
        },
        recaptcha: {
            widgets: [".g-recaptcha", "[data-sitekey][data-callback]",
                "iframe[src*='google.com/recaptcha']", "iframe[src*='recaptcha.net/recaptcha']"],
            blocking: ["iframe[src*='/recaptcha/api2/bframe']", "iframe[src*='/recaptcha/enterprise/bframe']"]
        },
        hcaptcha: {
            widgets: [".h-captcha", "iframe[src*='hcaptcha.com/captcha']"],
            blocking: ["iframe[src*='newassets.hcaptcha.com/captcha']"]
        },
        funcaptcha: {
            widgets: [".arkose", "[data-pkey]", "iframe[src*='arkoselabs.com']",
                "iframe[src*='funcaptcha.com']"],
            blocking: ["iframe[src*='/fc/gc/']"]
        },
        datadome: {
            widgets: ["iframe[src*='captcha-delivery.com']", "#datadome-captcha"],
            blocking: ["iframe[src*='geo.captcha-delivery.com']"]
        },
        aws_waf: {
            widgets: ["#aws-waf-captcha-container", "[data-aws-waf-captcha]",
                "script[src*='awswaf.com']"],
            blocking: ["iframe[src*='awswaf.com']"]
        },
        geetest: {
            widgets: [".geetest_holder", ".geetest_panel", "[class*='geetest_']"],
            blocking: [".geetest_panel"]
        },
        friendlycaptcha: {
            widgets: [".frc-captcha", "[data-sitekey][class*='frc-']"],
            blocking: []
        },
        mtcaptcha: {
            widgets: [".mtcaptcha", "iframe[src*='mtcaptcha.com']"],
            blocking: []
        },
        imperva: {
            widgets: ["iframe[src*='incapsula.com']", "iframe[src*='_Incapsula_Resource']"],
            blocking: ["#incapsula-incident-id", "iframe[src*='incapsula.com']"]
        },
        perimeterx: {
            widgets: ["#px-captcha", "iframe[src*='perimeterx.net']", "iframe[src*='humansecurity.com']"],
            blocking: ["#px-captcha"]
        }
    };
    const title = (document.title || "").toLowerCase();
    const bodyText = (document.body?.innerText || "").slice(0, 30000).toLowerCase();
    const challengePhrases = [
        "checking your browser", "verify you are human", "verification required",
        "complete the security check", "performing security verification",
        "unusual traffic", "attention required", "security challenge",
        "prove you are human", "confirm you are human", "bot verification",
        "press and hold", "slide to verify"
    ];
    const challengeText = challengePhrases.some((phrase) => title.includes(phrase)) ||
        (bodyText.length <= 5000 && challengePhrases.some((phrase) => bodyText.includes(phrase)));
    const signals = [];
    let widgetMatch = null;
    let blockingMatch = null;
    for (const [name, selectors] of Object.entries(providers)) {
        const widgets = query(selectors.widgets);
        const blocking = query(selectors.blocking);
        if (!widgets.length && !blocking.length) continue;
        const elements = [...widgets, ...blocking];
        const obstructed = elements.some(obstruction);
        const match = {name, elements, widgets, blocking, obstructed};
        if (blocking.length || obstructed) {
            blockingMatch = match;
            break;
        }
        if (!widgetMatch) widgetMatch = match;
    }
    const match = blockingMatch || widgetMatch;
    const provider = match?.name || null;
    const elements = match?.elements || [];
    const hasBlockingElement = Boolean(match?.blocking.length);
    const hasObstruction = Boolean(match?.obstructed);
    if (match?.widgets.length) signals.push("provider_widget");
    if (hasBlockingElement) signals.push("challenge_form");
    if (hasObstruction) signals.push("viewport_obstruction");
    if (status === 429) signals.push("main_response_429");
    else if ([401, 403, 503].includes(status)) signals.push(`main_response_${status}`);
    if (challengeText) signals.push("challenge_copy");
    const current = location.href.toLowerCase();
    const challengeUrl = /captcha|challenge|verify/.test(current);
    if (challengeUrl) signals.push("challenge_url");

    let kind = null;
    const blockingSignal = hasBlockingElement || hasObstruction || challengeText ||
        (challengeUrl && [401, 403, 429, 503].includes(status));
    if (status === 429 && blockingSignal) kind = "rate_limited";
    else if ([401, 403, 503].includes(status) && blockingSignal) kind = "access_denied";
    else if (blockingSignal) kind = "blocking_interstitial";
    else if (provider) kind = "embedded_widget";
    if (!kind) return null;

    let sitekey = null;
    for (const element of elements) {
        sitekey = element.getAttribute("data-sitekey") || element.getAttribute("data-pkey");
        if (sitekey) break;
        const src = element.getAttribute("src");
        if (!src) continue;
        try {
            const url = new URL(src, location.href);
            sitekey = url.searchParams.get("k") || url.searchParams.get("sitekey") ||
                url.searchParams.get("public_key");
            if (sitekey) break;
        } catch {}
    }
    const confidence = kind === "embedded_widget" ? 0.72 :
        (provider && signals.length >= 2 ? 0.98 : 0.88);
    return {provider: provider || "unknown", kind, confidence, signals,
        ...(sitekey ? {sitekey} : {})};
}"""


def load_captcha_handler(spec: str) -> CaptchaHandler | None:
    """Load an operator hook from ``module:function`` without bundling a solver."""
    if not spec:
        return None
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(
            "VIPERCAPTURE_CAPTCHA_HANDLER_FACTORY must use module:function syntax"
        )
    handler = getattr(importlib.import_module(module_name), attribute)()
    if not callable(handler):
        raise TypeError("CAPTCHA handler factory must return an async callable")
    return handler


async def detect_challenge(
    page: Any, navigation_status: int | None
) -> dict[str, object] | None:
    return await page.evaluate(DETECT_CHALLENGE_SCRIPT, {"status": navigation_status})


TURNSTILE_FRAME_SELECTORS = (
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='/cdn-cgi/challenge-platform/']",
    "iframe[title*='Cloudflare' i]",
    ".cf-turnstile iframe",
)
TURNSTILE_HOST_SELECTORS = (
    ".cf-turnstile",
    "#challenge-stage",
    "#challenge-form",
    "#challenge-running",
)
TURNSTILE_CHECKBOX_SELECTORS = (
    "input[type='checkbox']",
    "[role='checkbox']",
    "label",
    ".mark",
    "body",
)
POST_PASS_SETTLE_MS = 750
AUTO_PASS_POLL_S = 3.0
CLICK_TIMEOUT_MS = 2_500


def challenge_is_blocking(challenge: dict[str, object] | None) -> bool:
    if not challenge:
        return False
    return str(challenge.get("kind") or "") != "embedded_widget"


def is_cloudflare_challenge(challenge: dict[str, object] | None) -> bool:
    return bool(challenge) and str(challenge.get("provider") or "") == "cloudflare"


def _remaining_timeout_ms(deadline: float | None) -> int | None:
    if deadline is None:
        return None
    return max(0, int((deadline - time.monotonic()) * 1_000))


async def _click_locator(
    locator: Any,
    timeout_ms: int = CLICK_TIMEOUT_MS,
    *,
    deadline: float | None = None,
) -> bool:
    if locator is None:
        return False
    remaining = _remaining_timeout_ms(deadline)
    if remaining is not None:
        if remaining <= 0:
            return False
        timeout_ms = min(timeout_ms, remaining)
    click = getattr(locator, "click", None)
    if not callable(click):
        return False
    try:
        await click(timeout=timeout_ms)
        return True
    except Exception:
        return False


def _frame_candidates(page: Any) -> list[Any]:
    frames: list[Any] = []
    frame_locator = getattr(page, "frame_locator", None)
    if callable(frame_locator):
        for selector in TURNSTILE_FRAME_SELECTORS:
            try:
                frames.append(frame_locator(selector))
            except Exception:
                continue
    for frame in list(getattr(page, "frames", None) or []):
        url = str(getattr(frame, "url", "") or "").lower()
        if "challenges.cloudflare.com" in url or "cdn-cgi/challenge-platform" in url:
            frames.append(frame)
    return frames


async def click_turnstile_widget(
    page: Any,
    *,
    timeout_ms: int | None = None,
) -> bool:
    """Click the Turnstile checkbox via frames/locators. Never forges tokens."""
    deadline = (
        time.monotonic() + max(0, timeout_ms) / 1_000 if timeout_ms is not None else None
    )
    if deadline is not None and _remaining_timeout_ms(deadline) == 0:
        return False
    for frame in _frame_candidates(page):
        if deadline is not None and (_remaining_timeout_ms(deadline) or 0) <= 0:
            return False
        get_by_role = getattr(frame, "get_by_role", None)
        if callable(get_by_role):
            try:
                checkbox = get_by_role("checkbox")
            except Exception:
                checkbox = None
            if await _click_locator(checkbox, deadline=deadline):
                return True
        locator_factory = getattr(frame, "locator", None)
        if callable(locator_factory):
            for selector in TURNSTILE_CHECKBOX_SELECTORS:
                if deadline is not None and (_remaining_timeout_ms(deadline) or 0) <= 0:
                    return False
                try:
                    target = locator_factory(selector)
                    first = getattr(target, "first", target)
                except Exception:
                    continue
                if await _click_locator(first, deadline=deadline):
                    return True
    locator_factory = getattr(page, "locator", None)
    if callable(locator_factory):
        for selector in (*TURNSTILE_HOST_SELECTORS, *TURNSTILE_FRAME_SELECTORS):
            if deadline is not None and (_remaining_timeout_ms(deadline) or 0) <= 0:
                return False
            try:
                target = locator_factory(selector)
                first = getattr(target, "first", target)
            except Exception:
                continue
            if await _click_locator(first, deadline=deadline):
                return True
    return False


def challenge_cleared(challenge: dict[str, object] | None) -> bool:
    return challenge is None or not challenge_is_blocking(challenge)


async def wait_for_challenge_clear(
    page: Any,
    *,
    timeout_ms: int,
    navigation_status: int | None,
    poll_s: float = 0.25,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_ms / 1_000)
    while True:
        remaining = await detect_challenge(page, navigation_status)
        if challenge_cleared(remaining):
            return True
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0:
            return False
        await asyncio.sleep(min(poll_s, remaining_s))


async def complete_cloudflare_turnstile(
    page: Any,
    *,
    timeout_ms: int,
    navigation_status: int | None = None,
) -> bool:
    """Wait for a managed auto-pass, then click the checkbox and retry once.

    Interactive Turnstile (for example nowsecure.nl) may still remain after an
    honest click. Callers must treat that as an uncleared challenge.
    """
    budget_ms = max(1_000, timeout_ms)
    deadline = time.monotonic() + budget_ms / 1_000

    def remaining_ms() -> int:
        return max(0, int((deadline - time.monotonic()) * 1_000))

    auto_pass_ms = min(int(AUTO_PASS_POLL_S * 1_000), remaining_ms())
    if await wait_for_challenge_clear(
        page,
        timeout_ms=auto_pass_ms,
        navigation_status=navigation_status,
    ):
        if remaining_ms() > 0:
            await asyncio.sleep(min(POST_PASS_SETTLE_MS / 1_000, remaining_ms() / 1_000))
        return True

    click_budget = remaining_ms()
    if click_budget <= 0:
        return False
    clicked = await click_turnstile_widget(page, timeout_ms=click_budget)
    leftover = remaining_ms()
    first_wait = leftover // 2 if clicked else min(2_000, leftover)
    if leftover > 0 and await wait_for_challenge_clear(
        page,
        timeout_ms=first_wait,
        navigation_status=navigation_status,
    ):
        if remaining_ms() > 0:
            await asyncio.sleep(min(POST_PASS_SETTLE_MS / 1_000, remaining_ms() / 1_000))
        return True

    retry_budget = remaining_ms()
    if retry_budget <= 0:
        return False
    await click_turnstile_widget(page, timeout_ms=retry_budget)
    final_wait = remaining_ms()
    if final_wait > 0 and await wait_for_challenge_clear(
        page,
        timeout_ms=final_wait,
        navigation_status=navigation_status,
    ):
        if remaining_ms() > 0:
            await asyncio.sleep(min(POST_PASS_SETTLE_MS / 1_000, remaining_ms() / 1_000))
        return True
    return False


async def handle_challenge(
    page: Any,
    *,
    navigation_status: int | None,
    action: str,
    handler: CaptchaHandler | None,
    solver: str | None,
    timeout_ms: int,
    budget: Callable[[int], AsyncContextManager[None]] | None = None,
) -> None:
    challenge = await detect_challenge(page, navigation_status)
    if (
        turnstile_click_enabled()
        and is_cloudflare_challenge(challenge)
        and action != "external"
    ):
        native_timeout = min(timeout_ms, turnstile_timeout_ms())

        async def attempt_turnstile() -> bool:
            return await complete_cloudflare_turnstile(
                page,
                timeout_ms=native_timeout,
                navigation_status=navigation_status,
            )

        try:
            if budget is None:
                cleared = await attempt_turnstile()
            else:
                async with budget(native_timeout):
                    cleared = await attempt_turnstile()
        except TimeoutError:
            cleared = False
        if cleared:
            return
        challenge = await detect_challenge(page, None) or challenge
    if not challenge or challenge.get("kind") == "embedded_widget":
        return
    if action == "capture":
        return
    if action == "external":
        if handler is None:
            raise RenderError(
                "captcha_handler_unavailable",
                "No external CAPTCHA handler is configured.",
                503,
                False,
                challenge,
            )
        async def invoke_handler() -> bool:
            solved = handler(page, challenge, solver, timeout_ms)
            if not inspect.isawaitable(solved):
                raise TypeError("CAPTCHA handlers must return an awaitable")
            return await asyncio.wait_for(solved, timeout=timeout_ms / 1_000)

        try:
            if budget is None:
                cleared = await invoke_handler()
            else:
                async with budget(timeout_ms):
                    cleared = await invoke_handler()
        except TimeoutError as exc:
            raise RenderError(
                "captcha_handler_timeout",
                "The configured CAPTCHA handler timed out.",
                504,
                True,
                challenge,
            ) from exc
        if cleared:
            # The handler may have navigated; the original response status is stale.
            remaining = await detect_challenge(page, None)
            if not remaining or remaining.get("kind") == "embedded_widget":
                return
            challenge = remaining
        raise RenderError(
            "captcha_handler_failed",
            "The configured CAPTCHA handler did not clear the challenge.",
            409,
            False,
            challenge,
        )

    provider = str(challenge.get("provider") or "unknown")
    provider_label = {
        "cloudflare": "Cloudflare",
        "recaptcha": "Google reCAPTCHA",
        "hcaptcha": "hCaptcha",
        "funcaptcha": "Arkose Labs",
        "datadome": "DataDome",
        "aws_waf": "AWS WAF",
        "geetest": "GeeTest",
        "friendlycaptcha": "Friendly Captcha",
        "mtcaptcha": "MTCaptcha",
        "imperva": "Imperva",
        "perimeterx": "HUMAN/PerimeterX",
        "unknown": "A page-level",
    }.get(provider, provider.replace("_", " ").title())
    raise RenderError(
        "captcha_detected",
        f"{provider_label} challenge blocked the page.",
        409,
        False,
        challenge,
    )
