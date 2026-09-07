"""Challenge detection, Turnstile checkbox clicks, and an operator CAPTCHA hook.

Cloudflare Turnstile handling clicks a reachable checkbox through Patchright
frames/locators (including closed-shadow locators) with a human-like mouse
path, waits for a managed auto-pass, and retries with backoff. It does not
call solver APIs, mint tokens, or use undocumented Cloudflare endpoints.
Interactive Turnstile can still remain after an honest click.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, AsyncContextManager

from .browser_launch import turnstile_click_enabled, turnstile_timeout_ms
from .render_errors import RenderError

CaptchaHandler = Callable[[Any, dict[str, object], str | None, int], Awaitable[bool]]

# Managed Cloudflare pages use "Just a moment..." as the document title.
CHALLENGE_PHRASES: tuple[str, ...] = (
    "checking your browser",
    "just a moment",
    "verify you are human",
    "verification required",
    "complete the security check",
    "performing security verification",
    "needs to review the security of your connection",
    "checking if the site connection is secure",
    "enable javascript and cookies",
    "unusual traffic",
    "attention required",
    "security challenge",
    "prove you are human",
    "confirm you are human",
    "bot verification",
    "press and hold",
    "slide to verify",
)

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
            widgets: [".cf-turnstile", "iframe[src*='challenges.cloudflare.com']",
                "iframe[src*='turnstile']", "iframe[name^='cf-chl-widget']",
                "iframe[id^='cf-chl-widget']", "input[name='cf-turnstile-response']"],
            blocking: ["#challenge-stage", "#challenge-running", "#challenge-form",
                "#cf-challenge-running", "#cf-please-wait", ".cf-browser-verification",
                "#challenge-error-title", "iframe[src*='/cdn-cgi/challenge-platform/']"]
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
        "checking your browser", "just a moment", "verify you are human",
        "verification required", "complete the security check",
        "performing security verification",
        "needs to review the security of your connection",
        "checking if the site connection is secure",
        "enable javascript and cookies", "unusual traffic", "attention required",
        "security challenge", "prove you are human", "confirm you are human",
        "bot verification", "press and hold", "slide to verify"
    ];
    const challengeText = challengePhrases.some((phrase) => title.includes(phrase)) ||
        (bodyText.length <= 5000 && challengePhrases.some((phrase) => bodyText.includes(phrase)));
    const cfManaged = typeof window._cf_chl_opt !== "undefined" ||
        Boolean(document.querySelector("script[src*='cdn-cgi/challenge-platform']")) ||
        Boolean(document.querySelector("script[src*='challenges.cloudflare.com']"));
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
    let provider = match?.name || null;
    const elements = match?.elements || [];
    const hasBlockingElement = Boolean(match?.blocking.length);
    const hasObstruction = Boolean(match?.obstructed);
    if (match?.widgets.length) signals.push("provider_widget");
    if (hasBlockingElement) signals.push("challenge_form");
    if (hasObstruction) signals.push("viewport_obstruction");
    if (cfManaged) {
        signals.push("cf_challenge_script");
        if (!provider) provider = "cloudflare";
    }
    if (status === 429) signals.push("main_response_429");
    else if ([401, 403, 503].includes(status)) signals.push(`main_response_${status}`);
    if (challengeText) signals.push("challenge_copy");
    const current = location.href.toLowerCase();
    const challengeUrl = /captcha|challenge|verify|cdn-cgi/.test(current);
    if (challengeUrl) signals.push("challenge_url");
    const passPhrases = ["you bypassed", "you have been verified", "verification successful"];
    const passCopy = passPhrases.some((phrase) => title.includes(phrase)) ||
        passPhrases.some((phrase) => bodyText.includes(phrase));
    const tokenSelectors = ["input[name='cf-turnstile-response']",
        "textarea[name='cf-turnstile-response']"];
    const tokenFields = tokenSelectors.flatMap((selector) =>
        roots.flatMap((root) => [...root.querySelectorAll(selector)]));
    const hasToken = tokenFields.some((element) => String(element.value || "").trim().length > 0);
    const successUi = query(["#challenge-success", ".cf-turnstile[data-state='success']"]).length > 0;
    if (hasToken) signals.push("turnstile_token");
    if (passCopy) signals.push("bypass_copy");
    if (successUi) signals.push("challenge_success");
    // Stale navigation 403 must not keep a passed page uncleared.
    if (hasToken || passCopy || successUi) {
        return {provider: "cloudflare", kind: "passed", confidence: 1, signals};
    }

    let kind = null;
    const blockingSignal = hasBlockingElement || hasObstruction || challengeText ||
        cfManaged || (challengeUrl && [401, 403, 429, 503].includes(status));
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


TURNSTILE_FRAME_SELECTORS = (
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='/cdn-cgi/challenge-platform/']",
    "iframe[src*='turnstile']",
    "iframe[title*='Cloudflare' i]",
    "iframe[title*='security challenge' i]",
    "iframe[name^='cf-chl-widget']",
    "iframe[id^='cf-chl-widget']",
    "iframe[src*='cf-chl-widget']",
    ".cf-turnstile iframe",
)
TURNSTILE_HOST_SELECTORS = (
    ".cf-turnstile",
    "#challenge-stage",
    "#challenge-form",
    "#challenge-running",
    "#cf-challenge-running",
    "#cf-please-wait",
    ".cf-browser-verification",
)
TURNSTILE_BLOCKING_HOST_SELECTORS = (
    "#challenge-stage",
    "#challenge-form",
    "#challenge-running",
    "#cf-challenge-running",
    "#cf-please-wait",
    ".cf-browser-verification",
    "#challenge-error-title",
)
TURNSTILE_CHECKBOX_SELECTORS = (
    "input[type='checkbox']",
    "[role='checkbox']",
    "span[role='checkbox']",
    "div[role='checkbox']",
    "label",
    ".mark",
    "#cf-stage",
)
TURNSTILE_PASS_SIGNALS = frozenset(
    {"turnstile_token", "bypass_copy", "challenge_success"}
)
PASS_PHRASES: tuple[str, ...] = (
    "you bypassed",
    "you have been verified",
    "verification successful",
)
# Checkbox sits near the left-center of a typical 300x65 Turnstile widget.
TURNSTILE_CHECKBOX_POSITION = {"x": 28, "y": 32}
POST_PASS_SETTLE_MS = 750
AUTO_PASS_POLL_S = 8.0
CLICK_TIMEOUT_MS = 2_500
VISIBILITY_TIMEOUT_MS = 350
MAX_CLICK_ATTEMPTS = 3
CLICK_BACKOFF_MS = (400, 800, 1_600)
MOUSE_MOVE_STEPS = 12
CLICK_DELAY_MS = 70
POLL_S = 0.25


def challenge_is_blocking(challenge: dict[str, object] | None) -> bool:
    if not challenge:
        return False
    kind = str(challenge.get("kind") or "")
    return kind not in {"embedded_widget", "passed"}


def is_cloudflare_challenge(challenge: dict[str, object] | None) -> bool:
    """True for CF provider or locator/frame Turnstile hits (evaluate can be blind)."""
    if not challenge:
        return False
    if str(challenge.get("provider") or "") == "cloudflare":
        return True
    signals = {str(item) for item in (challenge.get("signals") or [])}
    return bool(
        signals
        & {
            "locator_widget",
            "locator_challenge_form",
            "turnstile_frame",
            "cf_challenge_script",
        }
    )


def challenge_cleared(challenge: dict[str, object] | None) -> bool:
    """Turnstile is cleared only when gone or a token/bypass/success signal exists.

    ``embedded_widget`` is not a pass: nowsecure's visible checkbox is an
    embedded widget, and treating it as cleared skipped the click.
    """
    if not challenge:
        return True
    if str(challenge.get("kind") or "") == "passed":
        return True
    signals = {str(item) for item in (challenge.get("signals") or [])}
    if signals & TURNSTILE_PASS_SIGNALS:
        return True
    if is_cloudflare_challenge(challenge):
        return False
    return not challenge_is_blocking(challenge)


def _remaining_timeout_ms(deadline: float | None) -> int | None:
    if deadline is None:
        return None
    return max(0, int((deadline - time.monotonic()) * 1_000))


def click_retry_wait_ms(attempt: int, remaining_ms: int) -> int:
    """Backoff before the next Turnstile click, capped by the remaining budget."""
    if remaining_ms <= 0:
        return 0
    index = min(max(0, attempt), len(CLICK_BACKOFF_MS) - 1)
    return min(CLICK_BACKOFF_MS[index], remaining_ms)


def post_click_wait_ms(remaining_ms: int, attempts_left: int, clicked: bool) -> int:
    """Share leftover time across this wait and any later click attempts."""
    if remaining_ms <= 0:
        return 0
    if not clicked:
        return min(2_000, remaining_ms)
    slots = max(1, attempts_left + 1)
    return max(1, remaining_ms // slots)


def auto_pass_wait_ms(budget_ms: int) -> int:
    """Spend up to AUTO_PASS_POLL_S, but leave room for at least one click+wait."""
    reserved = CLICK_TIMEOUT_MS + CLICK_BACKOFF_MS[0]
    if budget_ms <= reserved:
        return 0
    return min(int(AUTO_PASS_POLL_S * 1_000), budget_ms - reserved)


async def _call_maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _locator_is_visible(locator: Any, timeout_ms: int) -> bool | None:
    """True/False when the locator can report visibility; None if it cannot."""
    if locator is None:
        return False
    is_visible = getattr(locator, "is_visible", None)
    if callable(is_visible):
        try:
            return bool(await _call_maybe_await(is_visible(timeout=max(0, timeout_ms))))
        except TypeError:
            try:
                return bool(await _call_maybe_await(is_visible()))
            except Exception:
                return None
        except Exception:
            return False
    count = getattr(locator, "count", None)
    if callable(count):
        try:
            found = await _call_maybe_await(count())
            return int(found or 0) > 0
        except Exception:
            return False
    return None


def _first_locator(target: Any) -> Any:
    return getattr(target, "first", target)


def _supported_kwargs(func: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return dict(kwargs)
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return dict(kwargs)
    return {
        key: value
        for key, value in kwargs.items()
        if key in signature.parameters
    }


async def _invoke_with_supported_kwargs(func: Callable[..., Any], **kwargs: Any) -> Any:
    selected = _supported_kwargs(func, kwargs)
    try:
        return await _call_maybe_await(func(**selected))
    except TypeError:
        return await _call_maybe_await(func())


def _mouse_from_locator(locator: Any) -> Any | None:
    page = getattr(locator, "page", None) or getattr(locator, "_page", None)
    if page is None:
        return None
    mouse = getattr(page, "mouse", None)
    return mouse if mouse is not None else None


async def _mouse_click_at(mouse: Any, x: float, y: float) -> bool:
    move = getattr(mouse, "move", None)
    click_mouse = getattr(mouse, "click", None)
    if not callable(move) or not callable(click_mouse):
        return False
    try:
        try:
            await _call_maybe_await(move(x, y, steps=MOUSE_MOVE_STEPS))
        except TypeError:
            await _call_maybe_await(move(x, y))
        try:
            await _call_maybe_await(click_mouse(x, y, delay=CLICK_DELAY_MS))
        except TypeError:
            await _call_maybe_await(click_mouse(x, y))
        return True
    except Exception:
        return False


async def _human_click_locator(
    locator: Any,
    timeout_ms: int = CLICK_TIMEOUT_MS,
    *,
    deadline: float | None = None,
    position: dict[str, float] | None = None,
) -> bool:
    """Hover/mousemove then click when the APIs exist; never forges tokens."""
    if locator is None:
        return False
    remaining = _remaining_timeout_ms(deadline)
    if remaining is not None:
        if remaining <= 0:
            return False
        timeout_ms = min(timeout_ms, remaining)
    visible = await _locator_is_visible(
        locator, timeout_ms=min(VISIBILITY_TIMEOUT_MS, timeout_ms)
    )
    if visible is False:
        return False

    scroll = getattr(locator, "scroll_into_view_if_needed", None)
    if callable(scroll):
        try:
            await _invoke_with_supported_kwargs(scroll, timeout=timeout_ms)
        except Exception:
            pass

    box_fn = getattr(locator, "bounding_box", None)
    mouse = _mouse_from_locator(locator)
    if callable(box_fn) and mouse is not None:
        try:
            box = await _call_maybe_await(box_fn())
        except Exception:
            box = None
        if isinstance(box, dict) and box.get("width") and box.get("height"):
            x = float(box["x"]) + float(box["width"]) * 0.5
            y = float(box["y"]) + float(box["height"]) * 0.5
            if position:
                x = float(box["x"]) + min(
                    float(position.get("x", 0)), max(1.0, float(box["width"]) - 1)
                )
                y = float(box["y"]) + min(
                    float(position.get("y", 0)), max(1.0, float(box["height"]) - 1)
                )
            if await _mouse_click_at(mouse, x, y):
                return True

    hover = getattr(locator, "hover", None)
    if callable(hover):
        try:
            await _invoke_with_supported_kwargs(hover, timeout=timeout_ms)
        except Exception:
            pass

    click = getattr(locator, "click", None)
    if not callable(click):
        return False
    click_kwargs: dict[str, Any] = {
        "timeout": timeout_ms,
        "delay": CLICK_DELAY_MS,
    }
    if position:
        click_kwargs["position"] = position
    try:
        await _invoke_with_supported_kwargs(click, **click_kwargs)
        return True
    except Exception:
        return False


async def _click_locator(
    locator: Any,
    timeout_ms: int = CLICK_TIMEOUT_MS,
    *,
    deadline: float | None = None,
    position: dict[str, float] | None = None,
) -> bool:
    return await _human_click_locator(
        locator, timeout_ms, deadline=deadline, position=position
    )


def _turnstile_native_frames(page: Any) -> list[Any]:
    """Playwright ``page.frames`` can see closed-shadow CF iframes evaluate cannot."""
    matched: list[Any] = []
    for frame in list(getattr(page, "frames", None) or []):
        url = str(getattr(frame, "url", "") or "").lower()
        name = str(getattr(frame, "name", "") or "").lower()
        frame_id = str(getattr(frame, "name", "") or getattr(frame, "url", "") or "")
        if (
            "challenges.cloudflare.com" in url
            or "cdn-cgi/challenge-platform" in url
            or "turnstile" in url
            or "cf-chl-widget" in url
            or name.startswith("cf-chl-widget")
            or "cf-chl-widget" in frame_id.lower()
        ):
            matched.append(frame)
    return matched


def _frame_candidates(page: Any) -> list[Any]:
    frames: list[Any] = []
    frame_locator = getattr(page, "frame_locator", None)
    if callable(frame_locator):
        for selector in TURNSTILE_FRAME_SELECTORS:
            try:
                frames.append(frame_locator(selector))
            except Exception:
                continue
    frames.extend(_turnstile_native_frames(page))
    return frames


async def _click_checkbox_on(
    frame: Any,
    *,
    deadline: float | None,
) -> bool:
    get_by_role = getattr(frame, "get_by_role", None)
    if callable(get_by_role):
        try:
            checkbox = get_by_role("checkbox")
        except Exception:
            checkbox = None
        if await _click_locator(_first_locator(checkbox), deadline=deadline):
            return True
    locator_factory = getattr(frame, "locator", None)
    if callable(locator_factory):
        for selector in TURNSTILE_CHECKBOX_SELECTORS:
            if deadline is not None and (_remaining_timeout_ms(deadline) or 0) <= 0:
                return False
            try:
                target = _first_locator(locator_factory(selector))
            except Exception:
                continue
            if await _click_locator(target, deadline=deadline):
                return True
    return False


async def click_turnstile_widget(
    page: Any,
    *,
    timeout_ms: int | None = None,
) -> bool:
    """Click the Turnstile checkbox via frames/locators. Never forges tokens.

    Patchright locators can pierce closed shadow roots; this path prefers
    ``get_by_role('checkbox')`` and frame locators, then host widgets.
    """
    deadline = (
        time.monotonic() + max(0, timeout_ms) / 1_000 if timeout_ms is not None else None
    )
    if deadline is not None and _remaining_timeout_ms(deadline) == 0:
        return False
    for frame in _frame_candidates(page):
        if deadline is not None and (_remaining_timeout_ms(deadline) or 0) <= 0:
            return False
        if await _click_checkbox_on(frame, deadline=deadline):
            return True

    # Closed-shadow widgets on the page itself (Patchright locator pierce).
    if await _click_checkbox_on(page, deadline=deadline):
        return True

    locator_factory = getattr(page, "locator", None)
    if callable(locator_factory):
        for selector in (*TURNSTILE_HOST_SELECTORS, *TURNSTILE_FRAME_SELECTORS):
            if deadline is not None and (_remaining_timeout_ms(deadline) or 0) <= 0:
                return False
            try:
                target = _first_locator(locator_factory(selector))
            except Exception:
                continue
            iframe_like = "iframe" in selector
            position = TURNSTILE_CHECKBOX_POSITION if iframe_like else None
            if await _click_locator(target, deadline=deadline, position=position):
                return True
    return False


async def _locator_selector_hit(
    page: Any, selectors: Sequence[str], *, timeout_ms: int = VISIBILITY_TIMEOUT_MS
) -> bool:
    locator_factory = getattr(page, "locator", None)
    if not callable(locator_factory):
        return False
    for selector in selectors:
        try:
            target = _first_locator(locator_factory(selector))
        except Exception:
            continue
        visible = await _locator_is_visible(target, timeout_ms=timeout_ms)
        if visible is True:
            return True
    return False


async def locator_cloudflare_challenge(page: Any) -> dict[str, object] | None:
    """Detect Cloudflare widgets Patchright locators/frames can see (closed shadow)."""
    blocking = await _locator_selector_hit(page, TURNSTILE_BLOCKING_HOST_SELECTORS)
    widget = await _locator_selector_hit(
        page, (*TURNSTILE_HOST_SELECTORS, *TURNSTILE_FRAME_SELECTORS)
    )
    frame_hit = bool(_turnstile_native_frames(page))
    if not blocking and not widget and not frame_hit:
        return None
    # scrapingcourse: evaluate sees iframes=0 / provider=unknown, but frames exist.
    # Treat a native Turnstile frame as blocking so the click path runs.
    if blocking or frame_hit:
        kind = "blocking_interstitial"
    else:
        kind = "embedded_widget"
    signals = ["locator_widget"]
    if blocking:
        signals.append("locator_challenge_form")
    if frame_hit:
        signals.append("turnstile_frame")
    return {
        "provider": "cloudflare",
        "kind": kind,
        "confidence": 0.9 if kind == "blocking_interstitial" else 0.72,
        "signals": signals,
    }


def merge_challenge_signals(
    evaluated: dict[str, object] | None,
    locator_hit: dict[str, object] | None,
) -> dict[str, object] | None:
    """Combine in-page JS detection with Patchright locator/frame hits."""
    if not evaluated and not locator_hit:
        return None
    if evaluated and (
        str(evaluated.get("kind") or "") == "passed"
        or TURNSTILE_PASS_SIGNALS.intersection(
            str(item) for item in (evaluated.get("signals") or [])
        )
    ):
        return None
    if not evaluated:
        return dict(locator_hit or {})
    merged = dict(evaluated)
    if not locator_hit:
        return merged
    signals = list(merged.get("signals") or [])
    for signal in locator_hit.get("signals") or []:
        if signal not in signals:
            signals.append(str(signal))
    merged["signals"] = signals
    if not merged.get("provider") or merged.get("provider") == "unknown":
        merged["provider"] = locator_hit.get("provider") or "cloudflare"
        merged["confidence"] = max(
            float(merged.get("confidence") or 0),
            float(locator_hit.get("confidence") or 0),
        )
    if not challenge_is_blocking(merged) and challenge_is_blocking(locator_hit):
        merged["kind"] = locator_hit.get("kind")
        merged["confidence"] = max(
            float(merged.get("confidence") or 0),
            float(locator_hit.get("confidence") or 0),
        )
    return merged


async def detect_challenge(
    page: Any, navigation_status: int | None
) -> dict[str, object] | None:
    evaluated = None
    evaluate = getattr(page, "evaluate", None)
    if callable(evaluate):
        try:
            evaluated = await evaluate(DETECT_CHALLENGE_SCRIPT, {"status": navigation_status})
        except Exception:
            evaluated = None
    locator_hit = None
    try:
        locator_hit = await locator_cloudflare_challenge(page)
    except Exception:
        locator_hit = None
    return merge_challenge_signals(evaluated, locator_hit)


async def wait_for_challenge_clear(
    page: Any,
    *,
    timeout_ms: int,
    navigation_status: int | None,
    poll_s: float = POLL_S,
    stop_when: Callable[[], Awaitable[bool]] | None = None,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_ms / 1_000)
    interval = max(0.05, poll_s)
    first = True
    while True:
        status = navigation_status if first else None
        remaining = await detect_challenge(page, status)
        first = False
        if challenge_cleared(remaining):
            return True
        if stop_when is not None and await stop_when():
            return False
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0:
            return False
        await asyncio.sleep(min(interval, remaining_s))
        interval = min(1.0, interval * 1.4)


async def _checkbox_target_available(page: Any) -> bool:
    """Cheap probe: a checkbox or Turnstile iframe is already actionable."""
    for frame in _frame_candidates(page):
        get_by_role = getattr(frame, "get_by_role", None)
        if callable(get_by_role):
            try:
                checkbox = _first_locator(get_by_role("checkbox"))
            except Exception:
                checkbox = None
            visible = await _locator_is_visible(checkbox, timeout_ms=0)
            if visible is True:
                return True
        locator_factory = getattr(frame, "locator", None)
        if callable(locator_factory):
            for selector in ("input[type='checkbox']", "[role='checkbox']"):
                try:
                    target = _first_locator(locator_factory(selector))
                except Exception:
                    continue
                visible = await _locator_is_visible(target, timeout_ms=0)
                if visible is True:
                    return True
    get_by_role = getattr(page, "get_by_role", None)
    if callable(get_by_role):
        try:
            checkbox = _first_locator(get_by_role("checkbox"))
        except Exception:
            checkbox = None
        visible = await _locator_is_visible(checkbox, timeout_ms=0)
        if visible is True:
            return True
    return await _locator_selector_hit(
        page, TURNSTILE_FRAME_SELECTORS, timeout_ms=0
    )


async def complete_cloudflare_turnstile(
    page: Any,
    *,
    timeout_ms: int,
    navigation_status: int | None = None,
) -> bool:
    """Wait for a managed auto-pass, then click with backoff retries.

    Interactive Turnstile (for example nowsecure.nl) may still remain after an
    honest click. Callers must treat that as an uncleared challenge. This is
    not a Cloudflare bypass and does not call solvers.
    """
    budget_ms = max(1_000, timeout_ms)
    deadline = time.monotonic() + budget_ms / 1_000

    def remaining_ms() -> int:
        return max(0, int((deadline - time.monotonic()) * 1_000))

    async def _settle_if_cleared() -> bool:
        if remaining_ms() > 0:
            await asyncio.sleep(min(POST_PASS_SETTLE_MS / 1_000, remaining_ms() / 1_000))
        return True

    auto_pass_ms = auto_pass_wait_ms(remaining_ms())
    if auto_pass_ms > 0 and await wait_for_challenge_clear(
        page,
        timeout_ms=auto_pass_ms,
        navigation_status=navigation_status,
        stop_when=_checkbox_target_available,
    ):
        return await _settle_if_cleared()

    attempts_left = MAX_CLICK_ATTEMPTS
    for attempt in range(MAX_CLICK_ATTEMPTS):
        click_budget = remaining_ms()
        if click_budget <= 0:
            return False
        clicked = await click_turnstile_widget(page, timeout_ms=click_budget)
        attempts_left -= 1
        leftover = remaining_ms()
        wait_ms = post_click_wait_ms(leftover, max(1, attempts_left), clicked)
        if leftover > 0 and await wait_for_challenge_clear(
            page,
            timeout_ms=wait_ms,
            navigation_status=None,
        ):
            return await _settle_if_cleared()
        backoff = click_retry_wait_ms(attempt, remaining_ms())
        if attempts_left <= 0 or backoff <= 0:
            break
        await asyncio.sleep(backoff / 1_000)
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
        remaining = await detect_challenge(page, None)
        if remaining is None or challenge_cleared(remaining):
            return
        challenge = remaining
    if not challenge or challenge.get("kind") in {"embedded_widget", "passed"}:
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
            if not remaining or remaining.get("kind") in {"embedded_widget", "passed"}:
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
