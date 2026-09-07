"""Patchright Chromium launch knobs for ViperCapture Stealth.

Docker/GHCR keeps the isolated ``launch()`` + ``new_context()`` path with
headless bundled Chromium. Operators can opt into Patchright's documented
sweet spot: ``launch_persistent_context``, ``channel=chrome``, headed mode,
``no_viewport``, and no custom user-agent.

These helpers never spoof WebGL/canvas fingerprints. SwiftShader flags only
enable a software GL implementation so WebGL can create a context on GPU-less
Xvfb hosts.
"""

from __future__ import annotations

import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

_FALSE = {"0", "false", "no", "off"}


def env_value(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip()


def env_flag(name: str, default: bool = False) -> bool:
    raw = env_value(name)
    if raw is None:
        return default
    return raw.lower() not in _FALSE


def env_int(name: str, default: int) -> int:
    raw = env_value(name)
    if raw is None:
        return default
    return int(raw)


def running_in_docker() -> bool:
    return Path("/.dockerenv").exists() or env_flag("VIPERCAPTURE_IN_DOCKER")


def display_available() -> bool:
    return bool(env_value("DISPLAY") or env_value("WAYLAND_DISPLAY"))


def sweetspot_enabled() -> bool:
    """Composite opt-in for Patchright's headed persistent Chrome path."""
    return env_flag("VIPERCAPTURE_PATCHRIGHT_SWEETSPOT")


def persistent_context_enabled() -> bool:
    return env_flag("VIPERCAPTURE_PATCHRIGHT_PERSISTENT") or sweetspot_enabled()


def browser_channel() -> str:
    raw = (env_value("VIPERCAPTURE_BROWSER_CHANNEL") or "").lower()
    if raw in {"chromium", "chrome"}:
        return raw
    if raw:
        raise ValueError("VIPERCAPTURE_BROWSER_CHANNEL must be chromium or chrome")
    if sweetspot_enabled():
        return "chrome"
    return "chromium"


def headless_enabled() -> bool:
    raw = env_value("VIPERCAPTURE_HEADLESS")
    if raw is not None:
        return raw.lower() not in _FALSE
    if sweetspot_enabled():
        return False
    return True


def no_viewport_enabled(*, headless: bool | None = None, channel: str | None = None) -> bool:
    raw = env_value("VIPERCAPTURE_PATCHRIGHT_NO_VIEWPORT")
    if raw is not None:
        return raw.lower() not in _FALSE
    headed_chrome = not (headless if headless is not None else headless_enabled()) and (
        (channel or browser_channel()) == "chrome"
    )
    return persistent_context_enabled() and headed_chrome


def swiftshader_enabled() -> bool:
    """Software WebGL via SwiftShader. Not a fingerprint spoof."""
    return env_flag("VIPERCAPTURE_SWIFTSHADER")


def turnstile_click_enabled() -> bool:
    return env_flag("VIPERCAPTURE_TURNSTILE_CLICK", default=True)


def turnstile_timeout_ms() -> int:
    return max(1_000, min(120_000, env_int("VIPERCAPTURE_TURNSTILE_TIMEOUT_MS", 20_000)))


def omit_custom_user_agent(*, headless: bool | None = None, channel: str | None = None) -> bool:
    """Patchright guidance: do not inject a custom UA on headed Chrome."""
    if persistent_context_enabled():
        return True
    selected_channel = channel or browser_channel()
    selected_headless = headless_enabled() if headless is None else headless
    return selected_channel == "chrome" or not selected_headless


def persistent_user_data_dir(slot: int = 0) -> Path:
    override = env_value("VIPERCAPTURE_PATCHRIGHT_USER_DATA_DIR")
    if override:
        base = Path(override).expanduser()
    else:
        data = env_value("VIPERCAPTURE_DATA_DIR") or str(Path.home() / ".vipercapture")
        base = Path(data).expanduser() / "patchright-profiles"
    path = base / f"slot-{slot}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def chromium_launch_args(
    mode: str,
    backend: str = "default",
    *,
    swiftshader: bool | None = None,
    platform: str = sys.platform,
) -> list[str]:
    """GPU / software-GL Chromium flags. SwiftShader is used only when GPU is off."""
    use_swiftshader = swiftshader_enabled() if swiftshader is None else swiftshader
    args: list[str] = []
    if mode != "off":
        args.append("--enable-gpu")
        if backend == "vulkan" and platform.startswith("linux"):
            args.append("--use-angle=vulkan")
        return args
    if use_swiftshader:
        args.extend(
            [
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
            ]
        )
    return args


def chromium_cache_env() -> dict[str, str]:
    return {
        **os.environ,
        "XDG_CACHE_HOME": "/tmp/chromium-cache",
        "XDG_CONFIG_HOME": "/tmp/chromium-config",
    }


def chromium_launch_options(
    *,
    gpu_mode: str,
    gpu_backend: str = "default",
    headless: bool | None = None,
    channel: str | None = None,
    swiftshader: bool | None = None,
    platform: str = sys.platform,
) -> dict[str, object]:
    selected_headless = headless_enabled() if headless is None else headless
    selected_channel = channel or browser_channel()
    return {
        "headless": selected_headless,
        "args": chromium_launch_args(
            gpu_mode,
            gpu_backend,
            swiftshader=swiftshader,
            platform=platform,
        ),
        "channel": selected_channel,
        "env": chromium_cache_env(),
    }


def persistent_launch_options(
    *,
    gpu_mode: str,
    gpu_backend: str = "default",
    headless: bool | None = None,
    channel: str | None = None,
    swiftshader: bool | None = None,
    platform: str = sys.platform,
) -> dict[str, object]:
    options = chromium_launch_options(
        gpu_mode=gpu_mode,
        gpu_backend=gpu_backend,
        headless=headless,
        channel=channel,
        swiftshader=swiftshader,
        platform=platform,
    )
    selected_headless = bool(options["headless"])
    selected_channel = str(options["channel"])
    if no_viewport_enabled(headless=selected_headless, channel=selected_channel):
        options["no_viewport"] = True
    return options


def filter_persistent_context_options(
    options: dict[str, object],
    *,
    no_viewport: bool,
    omit_user_agent: bool,
) -> dict[str, object]:
    """Drop launch-incompatible kwargs so Patchright's sweet spot stays intact."""
    filtered = dict(options)
    if omit_user_agent:
        filtered.pop("user_agent", None)
        filtered.pop("extra_http_headers", None)
    if no_viewport:
        filtered.pop("viewport", None)
        filtered.pop("screen", None)
        filtered.pop("device_scale_factor", None)
    filtered.pop("no_viewport", None)
    return filtered


def headed_chrome_hint() -> str | None:
    """Operator hint when a display exists but headed Chrome is not configured."""
    if running_in_docker() or not display_available():
        return None
    if not headless_enabled() and browser_channel() == "chrome":
        return None
    return (
        "DISPLAY is set. Patchright's supported sweet spot is headed Google Chrome "
        "with a persistent profile: VIPERCAPTURE_BROWSER_CHANNEL=chrome "
        "VIPERCAPTURE_HEADLESS=0 VIPERCAPTURE_PATCHRIGHT_PERSISTENT=1. "
        "Docker/GHCR should keep headless bundled Chromium. Interactive Turnstile "
        "may still require a human or a site-owner allowlist; this is not a "
        "Cloudflare bypass."
    )


class BorrowedPersistentContext:
    """Closes pages opened for one render without shutting the persistent Chrome profile."""

    def __init__(self, context: Any):
        self._context = context
        self._pages: list[Any] = []

    async def new_page(self) -> Any:
        page = await self._context.new_page()
        self._pages.append(page)
        return page

    async def close(self) -> None:
        for page in list(self._pages):
            with suppress(Exception):
                await page.close()
        self._pages.clear()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)


class PersistentBrowser:
    """Browser-shaped wrapper around ``launch_persistent_context``."""

    def __init__(
        self,
        context: Any,
        *,
        no_viewport: bool,
        omit_user_agent: bool,
    ):
        self._context = context
        self._no_viewport = no_viewport
        self._omit_user_agent = omit_user_agent

    def is_connected(self) -> bool:
        browser = getattr(self._context, "browser", None)
        return bool(browser is not None and browser.is_connected())

    async def close(self) -> None:
        await self._context.close()

    async def new_browser_cdp_session(self) -> Any:
        browser = getattr(self._context, "browser", None)
        if browser is None:
            raise RuntimeError("persistent context has no browser")
        return await browser.new_browser_cdp_session()

    async def new_page(self) -> Any:
        return await self._context.new_page()

    async def new_context(self, **kwargs: object) -> BorrowedPersistentContext:
        filter_persistent_context_options(
            kwargs,
            no_viewport=self._no_viewport,
            omit_user_agent=self._omit_user_agent,
        )
        storage_state = kwargs.get("storage_state")
        if isinstance(storage_state, dict):
            cookies = storage_state.get("cookies")
            if cookies:
                await self._context.add_cookies(cookies)
        return BorrowedPersistentContext(self._context)


_persistent_slots = 0


def next_persistent_slot() -> int:
    global _persistent_slots
    slot = _persistent_slots
    _persistent_slots += 1
    return slot


async def launch_chromium(
    playwright: Any,
    *,
    gpu_mode: str,
    gpu_backend: str = "default",
    persistent: bool | None = None,
    headless: bool | None = None,
    channel: str | None = None,
    swiftshader: bool | None = None,
    user_data_dir: Path | None = None,
    platform: str = sys.platform,
) -> Any:
    """Launch bundled Chromium/Chrome, or a persistent context when opted in."""
    use_persistent = persistent_context_enabled() if persistent is None else persistent
    selected_headless = headless_enabled() if headless is None else headless
    selected_channel = channel or browser_channel()
    if use_persistent:
        options = persistent_launch_options(
            gpu_mode=gpu_mode,
            gpu_backend=gpu_backend,
            headless=selected_headless,
            channel=selected_channel,
            swiftshader=swiftshader,
            platform=platform,
        )
        profile = user_data_dir or persistent_user_data_dir(next_persistent_slot())
        context = await playwright.chromium.launch_persistent_context(
            str(profile),
            **options,
        )
        return PersistentBrowser(
            context,
            no_viewport=bool(options.get("no_viewport")),
            omit_user_agent=omit_custom_user_agent(
                headless=selected_headless,
                channel=selected_channel,
            ),
        )
    options = chromium_launch_options(
        gpu_mode=gpu_mode,
        gpu_backend=gpu_backend,
        headless=selected_headless,
        channel=selected_channel,
        swiftshader=swiftshader,
        platform=platform,
    )
    return await playwright.chromium.launch(**options)
