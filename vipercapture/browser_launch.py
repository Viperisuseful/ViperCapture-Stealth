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

import asyncio
import json
import os
import shutil
import sys
import tempfile
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
    """Headed persistent Chrome: explicit flag, or a workstation display.

    When ``DISPLAY`` / ``WAYLAND_DISPLAY`` is set and we are not in Docker,
    default to Patchright's supported sweet spot (persistent + chrome + headed
    + ``no_viewport``). Docker/GHCR often has ``DISPLAY`` for Xvfb and must
    keep headless bundled Chromium unless the operator opts in.
    """
    raw = env_value("VIPERCAPTURE_PATCHRIGHT_SWEETSPOT")
    if raw is not None:
        return raw.lower() not in _FALSE
    if running_in_docker():
        return False
    return display_available()


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
    return max(1_000, min(120_000, env_int("VIPERCAPTURE_TURNSTILE_TIMEOUT_MS", 30_000)))


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


def video_recording_options(options: dict[str, object]) -> dict[str, object]:
    """Keep Playwright ``record_video_*`` kwargs for dedicated persistent launches."""
    return {
        key: value
        for key, value in options.items()
        if str(key).startswith("record_video_") and value is not None
    }


def slot_from_user_data_dir(path: Path) -> int | None:
    name = Path(path).name
    if not name.startswith("slot-"):
        return None
    try:
        return int(name.removeprefix("slot-"))
    except ValueError:
        return None


def _normalize_storage_state(storage_state: object) -> dict[str, object] | None:
    if storage_state is None:
        return None
    if isinstance(storage_state, dict):
        return storage_state
    if isinstance(storage_state, (str, Path)):
        document = json.loads(Path(storage_state).read_text(encoding="utf-8"))
        if isinstance(document, dict):
            return document
    return None


_APPLY_ORIGIN_STORAGE = """({ items, clear }) => {
    try {
        if (clear) {
            localStorage.clear();
            sessionStorage.clear();
        }
        for (const item of items || []) {
            if (!item || item.name == null) continue;
            localStorage.setItem(String(item.name), String(item.value ?? ""));
        }
    } catch {}
}"""


async def _empty_origin_route(route: Any) -> None:
    fulfill = getattr(route, "fulfill", None)
    if callable(fulfill):
        await fulfill(status=200, content_type="text/html", body="<html></html>")
        return
    continue_ = getattr(route, "continue_", None)
    if callable(continue_):
        await continue_()


async def _visit_origin_storage(
    context: Any,
    origin: str,
    *,
    items: list[object] | None = None,
    clear: bool = False,
) -> None:
    new_page = getattr(context, "new_page", None)
    if not callable(new_page) or not origin:
        return
    page = await new_page()
    try:
        route = getattr(page, "route", None)
        if callable(route):
            with suppress(Exception):
                await route("**/*", _empty_origin_route)
        goto = getattr(page, "goto", None)
        if callable(goto):
            await goto(origin, wait_until="domcontentloaded", timeout=5_000)
        evaluate = getattr(page, "evaluate", None)
        if callable(evaluate):
            await evaluate(
                _APPLY_ORIGIN_STORAGE,
                {"items": list(items or []), "clear": clear},
            )
    finally:
        with suppress(Exception):
            await page.close()


async def reset_persistent_browser_state(context: Any) -> None:
    """Drop cookies and origin storage so the next render cannot inherit a session."""
    snapshot = None
    storage_state = getattr(context, "storage_state", None)
    if callable(storage_state):
        with suppress(Exception):
            snapshot = await storage_state()
    clear_cookies = getattr(context, "clear_cookies", None)
    if callable(clear_cookies):
        with suppress(Exception):
            await clear_cookies()
    origins: list[object] = []
    if isinstance(snapshot, dict):
        raw_origins = snapshot.get("origins")
        if isinstance(raw_origins, list):
            origins = raw_origins
    for entry in origins:
        if not isinstance(entry, dict):
            continue
        origin = str(entry.get("origin") or "")
        if not origin:
            continue
        with suppress(Exception):
            await _visit_origin_storage(context, origin, clear=True)


async def apply_persistent_storage_state(context: Any, storage_state: object) -> None:
    """Install cookies and origin localStorage onto an already-launched profile."""
    document = _normalize_storage_state(storage_state)
    if not document:
        return
    cookies = document.get("cookies")
    if cookies:
        add_cookies = getattr(context, "add_cookies", None)
        if callable(add_cookies):
            await add_cookies(cookies)
    origins = document.get("origins")
    if not isinstance(origins, list):
        return
    for entry in origins:
        if not isinstance(entry, dict):
            continue
        origin = str(entry.get("origin") or "")
        items = entry.get("localStorage")
        if not origin or not items:
            continue
        with suppress(Exception):
            await _visit_origin_storage(context, origin, items=list(items), clear=False)


def headed_chrome_hint() -> str | None:
    """Operator hint when a display exists but headed Chrome is not configured."""
    if running_in_docker() or not display_available():
        return None
    if not headless_enabled() and browser_channel() == "chrome":
        return None
    return (
        "DISPLAY is set. Patchright's supported sweet spot is headed Google Chrome "
        "with a persistent profile (persistent + chrome + headed + no_viewport). "
        "Set VIPERCAPTURE_PATCHRIGHT_SWEETSPOT=0 to keep headless Chromium. "
        "Docker/GHCR should keep headless bundled Chromium. Interactive Turnstile "
        "may still require a human or a site-owner allowlist; this is not a "
        "Cloudflare bypass."
    )


class BorrowedPersistentContext:
    """Closes pages opened for one render without shutting the persistent Chrome profile."""

    def __init__(
        self,
        context: Any,
        *,
        owns_context: bool = False,
        isolate_on_close: bool = False,
        render_lock: asyncio.Lock | None = None,
        scratch_profile: Path | None = None,
    ):
        self._context = context
        self._pages: list[Any] = []
        self._owns_context = owns_context
        self._isolate_on_close = isolate_on_close
        self._render_lock = render_lock
        self._scratch_profile = scratch_profile
        self._released = False

    async def new_page(self) -> Any:
        page = await self._context.new_page()
        self._pages.append(page)
        return page

    async def close(self) -> None:
        try:
            for page in list(self._pages):
                with suppress(Exception):
                    await page.close()
            self._pages.clear()
            if self._owns_context:
                with suppress(Exception):
                    await self._context.close()
                if self._scratch_profile is not None:
                    shutil.rmtree(self._scratch_profile, ignore_errors=True)
            elif self._isolate_on_close:
                await reset_persistent_browser_state(self._context)
        finally:
            if self._render_lock is not None and not self._released:
                self._released = True
                if self._render_lock.locked():
                    self._render_lock.release()

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
        playwright: Any | None = None,
        launch_options: dict[str, object] | None = None,
        slot: int | None = None,
        user_data_dir: Path | None = None,
    ):
        self._context = context
        self._no_viewport = no_viewport
        self._omit_user_agent = omit_user_agent
        self._playwright = playwright
        self._launch_options = dict(launch_options or {})
        self.persistent_slot = slot
        self.user_data_dir = Path(user_data_dir) if user_data_dir is not None else None
        self._render_lock = asyncio.Lock()
        self._slot_released = False

    def is_connected(self) -> bool:
        browser = getattr(self._context, "browser", None)
        return bool(browser is not None and browser.is_connected())

    async def close(self) -> None:
        try:
            await self._context.close()
        finally:
            self._release_slot()

    def _release_slot(self) -> None:
        if self._slot_released:
            return
        self._slot_released = True
        if self.persistent_slot is not None:
            release_persistent_slot(self.persistent_slot)

    async def new_browser_cdp_session(self) -> Any:
        browser = getattr(self._context, "browser", None)
        if browser is None:
            raise RuntimeError("persistent context has no browser")
        return await browser.new_browser_cdp_session()

    async def new_page(self) -> Any:
        return await self._context.new_page()

    async def new_context(self, **kwargs: object) -> BorrowedPersistentContext:
        filtered = filter_persistent_context_options(
            kwargs,
            no_viewport=self._no_viewport,
            omit_user_agent=self._omit_user_agent,
        )
        video_options = video_recording_options(filtered)
        if video_options.get("record_video_dir"):
            return await self._launch_recording_context(filtered, video_options)
        await self._render_lock.acquire()
        transferred = False
        try:
            await reset_persistent_browser_state(self._context)
            await apply_persistent_storage_state(
                self._context, filtered.get("storage_state")
            )
            borrowed = BorrowedPersistentContext(
                self._context,
                isolate_on_close=True,
                render_lock=self._render_lock,
            )
            transferred = True
            return borrowed
        finally:
            if not transferred and self._render_lock.locked():
                self._render_lock.release()

    async def _launch_recording_context(
        self,
        filtered: dict[str, object],
        video_options: dict[str, object],
    ) -> BorrowedPersistentContext:
        recording_kwargs = dict(filtered)
        recording_kwargs.update(video_options)
        browser = getattr(self._context, "browser", None)
        new_context = getattr(browser, "new_context", None) if browser is not None else None
        if callable(new_context):
            try:
                context = await new_context(**recording_kwargs)
                return BorrowedPersistentContext(context, owns_context=True)
            except Exception:
                pass
        if self._playwright is None:
            raise RuntimeError(
                "persistent video recording requires launch_persistent_context "
                "or browser.new_context"
            )
        options = dict(self._launch_options)
        options.update(recording_kwargs)
        scratch = Path(tempfile.mkdtemp(prefix="vipercapture-pr-video-"))
        try:
            context = await self._playwright.chromium.launch_persistent_context(
                str(scratch),
                **options,
            )
        except BaseException:
            shutil.rmtree(scratch, ignore_errors=True)
            raise
        return BorrowedPersistentContext(
            context,
            owns_context=True,
            scratch_profile=scratch,
        )


_used_persistent_slots: set[int] = set()


def reset_persistent_slots() -> None:
    _used_persistent_slots.clear()


def used_persistent_slots() -> set[int]:
    return set(_used_persistent_slots)


def release_persistent_slot(slot: int) -> None:
    _used_persistent_slots.discard(slot)


def acquire_persistent_slot(slot: int) -> int:
    """Reserve ``slot`` when free; otherwise take the lowest unused slot."""
    if slot not in _used_persistent_slots:
        _used_persistent_slots.add(slot)
        return slot
    return next_persistent_slot()


def next_persistent_slot() -> int:
    slot = 0
    while slot in _used_persistent_slots:
        slot += 1
    _used_persistent_slots.add(slot)
    return slot


def _reserve_profile_slot(user_data_dir: Path | None) -> tuple[Path, int | None]:
    if user_data_dir is not None:
        profile = Path(user_data_dir)
        profile.mkdir(parents=True, exist_ok=True)
        named = slot_from_user_data_dir(profile)
        if named is not None:
            _used_persistent_slots.add(named)
            return profile, named
        return profile, None
    slot = next_persistent_slot()
    return persistent_user_data_dir(slot), slot


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
        profile, slot = _reserve_profile_slot(user_data_dir)
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(profile),
                **options,
            )
        except Exception:
            if slot is not None:
                release_persistent_slot(slot)
            raise
        return PersistentBrowser(
            context,
            no_viewport=bool(options.get("no_viewport")),
            omit_user_agent=omit_custom_user_agent(
                headless=selected_headless,
                channel=selected_channel,
            ),
            playwright=playwright,
            launch_options=options,
            slot=slot,
            user_data_dir=profile,
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
