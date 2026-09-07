from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vipercapture.browser_launch import (  # noqa: E402
    BorrowedPersistentContext,
    PersistentBrowser,
    apply_persistent_storage_state,
    browser_channel,
    chromium_launch_args,
    chromium_launch_options,
    display_available,
    filter_persistent_context_options,
    headed_chrome_hint,
    headless_enabled,
    launch_chromium,
    next_persistent_slot,
    no_viewport_enabled,
    omit_custom_user_agent,
    persistent_context_enabled,
    persistent_launch_options,
    persistent_user_data_dir,
    release_persistent_slot,
    reset_persistent_browser_state,
    reset_persistent_slots,
    slot_from_user_data_dir,
    sweetspot_enabled,
    swiftshader_enabled,
    turnstile_click_enabled,
    turnstile_timeout_ms,
    used_persistent_slots,
    video_recording_options,
)


def _clear_launch_env() -> None:
    for name in (
        "VIPERCAPTURE_PATCHRIGHT_PERSISTENT",
        "VIPERCAPTURE_PATCHRIGHT_SWEETSPOT",
        "VIPERCAPTURE_BROWSER_CHANNEL",
        "VIPERCAPTURE_HEADLESS",
        "VIPERCAPTURE_PATCHRIGHT_NO_VIEWPORT",
        "VIPERCAPTURE_SWIFTSHADER",
        "VIPERCAPTURE_IN_DOCKER",
        "DISPLAY",
        "WAYLAND_DISPLAY",
    ):
        os.environ.pop(name, None)


class EnvKnobTests(unittest.TestCase):
    def test_persistent_and_sweetspot_default_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            _clear_launch_env()
            self.assertFalse(persistent_context_enabled())
            self.assertFalse(sweetspot_enabled())
            self.assertEqual(browser_channel(), "chromium")
            self.assertTrue(headless_enabled())
            self.assertFalse(no_viewport_enabled())
            self.assertFalse(swiftshader_enabled())
            self.assertTrue(turnstile_click_enabled())
            self.assertEqual(turnstile_timeout_ms(), 30_000)

    def test_display_defaults_to_sweetspot_outside_docker(self) -> None:
        with mock.patch.dict(os.environ, {"DISPLAY": ":1"}, clear=False):
            os.environ.pop("VIPERCAPTURE_PATCHRIGHT_SWEETSPOT", None)
            os.environ.pop("VIPERCAPTURE_PATCHRIGHT_PERSISTENT", None)
            os.environ.pop("VIPERCAPTURE_BROWSER_CHANNEL", None)
            os.environ.pop("VIPERCAPTURE_HEADLESS", None)
            os.environ.pop("VIPERCAPTURE_PATCHRIGHT_NO_VIEWPORT", None)
            os.environ.pop("VIPERCAPTURE_IN_DOCKER", None)
            with mock.patch(
                "vipercapture.browser_launch.running_in_docker", return_value=False
            ):
                self.assertTrue(sweetspot_enabled())
                self.assertTrue(persistent_context_enabled())
                self.assertEqual(browser_channel(), "chrome")
                self.assertFalse(headless_enabled())
                self.assertTrue(no_viewport_enabled())
                self.assertTrue(omit_custom_user_agent())

    def test_display_does_not_force_headed_in_docker(self) -> None:
        with mock.patch.dict(os.environ, {"DISPLAY": ":99"}, clear=False):
            os.environ.pop("VIPERCAPTURE_PATCHRIGHT_SWEETSPOT", None)
            os.environ.pop("VIPERCAPTURE_PATCHRIGHT_PERSISTENT", None)
            os.environ.pop("VIPERCAPTURE_BROWSER_CHANNEL", None)
            os.environ.pop("VIPERCAPTURE_HEADLESS", None)
            with mock.patch(
                "vipercapture.browser_launch.running_in_docker", return_value=True
            ):
                self.assertFalse(sweetspot_enabled())
                self.assertFalse(persistent_context_enabled())
                self.assertEqual(browser_channel(), "chromium")
                self.assertTrue(headless_enabled())
                self.assertFalse(no_viewport_enabled())

    def test_explicit_sweetspot_off_wins_over_display(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"DISPLAY": ":1", "VIPERCAPTURE_PATCHRIGHT_SWEETSPOT": "0"},
            clear=False,
        ):
            os.environ.pop("VIPERCAPTURE_HEADLESS", None)
            os.environ.pop("VIPERCAPTURE_BROWSER_CHANNEL", None)
            with mock.patch(
                "vipercapture.browser_launch.running_in_docker", return_value=False
            ):
                self.assertFalse(sweetspot_enabled())
                self.assertEqual(browser_channel(), "chromium")
                self.assertTrue(headless_enabled())

    def test_persistent_flag_enables_launch_path(self) -> None:
        with mock.patch.dict(os.environ, {"VIPERCAPTURE_PATCHRIGHT_PERSISTENT": "1"}):
            self.assertTrue(persistent_context_enabled())

    def test_sweetspot_selects_headed_chrome_persistent(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"VIPERCAPTURE_PATCHRIGHT_SWEETSPOT": "1"},
            clear=False,
        ):
            os.environ.pop("VIPERCAPTURE_BROWSER_CHANNEL", None)
            os.environ.pop("VIPERCAPTURE_HEADLESS", None)
            os.environ.pop("VIPERCAPTURE_PATCHRIGHT_PERSISTENT", None)
            os.environ.pop("VIPERCAPTURE_PATCHRIGHT_NO_VIEWPORT", None)
            self.assertTrue(sweetspot_enabled())
            self.assertTrue(persistent_context_enabled())
            self.assertEqual(browser_channel(), "chrome")
            self.assertFalse(headless_enabled())
            self.assertTrue(no_viewport_enabled())
            self.assertTrue(omit_custom_user_agent())

    def test_docker_headless_env_wins_over_sweetspot(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "VIPERCAPTURE_PATCHRIGHT_SWEETSPOT": "1",
                "VIPERCAPTURE_HEADLESS": "1",
                "VIPERCAPTURE_BROWSER_CHANNEL": "chromium",
            },
        ):
            self.assertTrue(headless_enabled())
            self.assertEqual(browser_channel(), "chromium")
            self.assertFalse(no_viewport_enabled())

    def test_explicit_no_viewport_and_channel(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "VIPERCAPTURE_PATCHRIGHT_NO_VIEWPORT": "1",
                "VIPERCAPTURE_BROWSER_CHANNEL": "chrome",
                "VIPERCAPTURE_HEADLESS": "0",
            },
        ):
            self.assertEqual(browser_channel(), "chrome")
            self.assertFalse(headless_enabled())
            self.assertTrue(no_viewport_enabled())

    def test_invalid_channel_raises(self) -> None:
        with mock.patch.dict(os.environ, {"VIPERCAPTURE_BROWSER_CHANNEL": "firefox"}):
            with self.assertRaises(ValueError):
                browser_channel()

    def test_turnstile_click_can_be_disabled(self) -> None:
        with mock.patch.dict(os.environ, {"VIPERCAPTURE_TURNSTILE_CLICK": "0"}):
            self.assertFalse(turnstile_click_enabled())


class LaunchOptionTests(unittest.TestCase):
    def test_default_launch_is_headless_chromium_without_swiftshader(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            _clear_launch_env()
            options = chromium_launch_options(gpu_mode="off")
            self.assertTrue(options["headless"])
            self.assertEqual(options["channel"], "chromium")
            self.assertEqual(options["args"], [])
            self.assertNotIn("no_viewport", options)

    def test_swiftshader_args_only_when_gpu_off(self) -> None:
        args = chromium_launch_args("off", swiftshader=True)
        self.assertIn("--use-angle=swiftshader", args)
        self.assertIn("--enable-unsafe-swiftshader", args)
        hardware = chromium_launch_args("auto", swiftshader=True)
        self.assertEqual(hardware, ["--enable-gpu"])
        self.assertNotIn("--use-angle=swiftshader", hardware)

    def test_persistent_headed_chrome_sets_no_viewport(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "VIPERCAPTURE_PATCHRIGHT_PERSISTENT": "1",
                "VIPERCAPTURE_BROWSER_CHANNEL": "chrome",
                "VIPERCAPTURE_HEADLESS": "0",
            },
        ):
            options = persistent_launch_options(gpu_mode="off")
            self.assertFalse(options["headless"])
            self.assertEqual(options["channel"], "chrome")
            self.assertTrue(options.get("no_viewport"))

    def test_filter_drops_custom_ua_and_viewport_for_sweet_spot(self) -> None:
        filtered = filter_persistent_context_options(
            {
                "user_agent": "Custom UA",
                "viewport": {"width": 800, "height": 600},
                "screen": {"width": 800, "height": 600},
                "locale": "en-US",
            },
            no_viewport=True,
            omit_user_agent=True,
        )
        self.assertNotIn("user_agent", filtered)
        self.assertNotIn("viewport", filtered)
        self.assertNotIn("screen", filtered)
        self.assertEqual(filtered["locale"], "en-US")

    def test_persistent_user_data_dir_is_durable_and_slot_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {"VIPERCAPTURE_PATCHRIGHT_USER_DATA_DIR": tmp}
            ):
                first = persistent_user_data_dir(0)
                second = persistent_user_data_dir(1)
                self.assertTrue(first.is_dir())
                self.assertNotEqual(first, second)
                self.assertEqual(first.parent, Path(tmp))

    def test_headed_chrome_hint_when_display_set(self) -> None:
        with mock.patch.dict(os.environ, {"DISPLAY": ":1", "VIPERCAPTURE_HEADLESS": "1"}):
            os.environ.pop("VIPERCAPTURE_IN_DOCKER", None)
            with mock.patch(
                "vipercapture.browser_launch.running_in_docker", return_value=False
            ):
                hint = headed_chrome_hint()
        self.assertIsNotNone(hint)
        self.assertIn("no_viewport", hint or "")
        self.assertIn("not a Cloudflare bypass", hint or "")

    def test_hint_skipped_in_docker(self) -> None:
        with mock.patch(
            "vipercapture.browser_launch.running_in_docker", return_value=True
        ):
            with mock.patch(
                "vipercapture.browser_launch.display_available", return_value=True
            ):
                self.assertIsNone(headed_chrome_hint())


class PersistentWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_persistent_slots()
        self.addCleanup(reset_persistent_slots)

    def test_borrowed_context_closes_pages_not_profile(self) -> None:
        closed: list[str] = []

        class FakePage:
            async def close(self) -> None:
                closed.append("page")

        class FakeContext:
            def __init__(self) -> None:
                self.closed = False

            async def new_page(self) -> FakePage:
                return FakePage()

            async def close(self) -> None:
                self.closed = True

        async def run() -> None:
            context = FakeContext()
            borrowed = BorrowedPersistentContext(context)
            await borrowed.new_page()
            await borrowed.close()
            self.assertEqual(closed, ["page"])
            self.assertFalse(context.closed)

        asyncio.run(run())

    def test_persistent_browser_new_context_applies_profile_cookies(self) -> None:
        added: list[object] = []

        class FakeInner:
            browser = None

            async def add_cookies(self, cookies: list[object]) -> None:
                added.extend(cookies)

            async def new_page(self) -> object:
                return object()

        async def run() -> None:
            browser = PersistentBrowser(
                FakeInner(), no_viewport=True, omit_user_agent=True
            )
            borrowed = await browser.new_context(
                user_agent="should-drop",
                storage_state={"cookies": [{"name": "session"}]},
            )
            self.assertIsInstance(borrowed, BorrowedPersistentContext)
            self.assertEqual(added, [{"name": "session"}])

        asyncio.run(run())

    def test_new_context_uses_filtered_options_return_value(self) -> None:
        added: list[object] = []

        class FakeInner:
            browser = None

            async def add_cookies(self, cookies: list[object]) -> None:
                added.extend(cookies)

        async def run() -> None:
            browser = PersistentBrowser(
                FakeInner(), no_viewport=True, omit_user_agent=True
            )
            with mock.patch(
                "vipercapture.browser_launch.filter_persistent_context_options",
                return_value={"storage_state": {"cookies": [{"name": "from-filter"}]}},
            ) as filtered:
                await browser.new_context(
                    user_agent="Custom UA",
                    storage_state={"cookies": [{"name": "original"}]},
                )
            filtered.assert_called_once()
            self.assertEqual(added, [{"name": "from-filter"}])

        asyncio.run(run())

    def test_new_context_isolates_cookies_and_applies_origins(self) -> None:
        class FakePage:
            def __init__(self, owner: "FakeInner") -> None:
                self.owner = owner
                self.closed = False

            async def route(self, _pattern: str, _handler: object) -> None:
                return None

            async def goto(self, origin: str, **_kwargs: object) -> None:
                self.owner.visits.append(origin)

            async def evaluate(self, _script: str, payload: dict[str, object]) -> None:
                origin = self.owner.visits[-1] if self.owner.visits else ""
                if payload.get("clear"):
                    self.owner.storage.pop(origin, None)
                    return
                self.owner.storage[origin] = {
                    item["name"]: item["value"]
                    for item in payload.get("items") or []
                }

            async def close(self) -> None:
                self.closed = True

        class FakeInner:
            browser = None

            def __init__(self) -> None:
                self.cookies: list[dict[str, object]] = [{"name": "stale", "value": "1"}]
                self.origins: list[dict[str, object]] = [
                    {
                        "origin": "https://stale.example",
                        "localStorage": [{"name": "old", "value": "yes"}],
                    }
                ]
                self.storage = {"https://stale.example": {"old": "yes"}}
                self.visits: list[str] = []

            async def storage_state(self) -> dict[str, object]:
                return {
                    "cookies": list(self.cookies),
                    "origins": [
                        {
                            "origin": origin,
                            "localStorage": [
                                {"name": name, "value": value}
                                for name, value in items.items()
                            ],
                        }
                        for origin, items in self.storage.items()
                    ],
                }

            async def clear_cookies(self) -> None:
                self.cookies.clear()

            async def add_cookies(self, cookies: list[dict[str, object]]) -> None:
                self.cookies.extend(cookies)

            async def new_page(self) -> FakePage:
                return FakePage(self)

        async def run() -> None:
            inner = FakeInner()
            browser = PersistentBrowser(
                inner, no_viewport=True, omit_user_agent=True
            )
            borrowed = await browser.new_context(
                storage_state={
                    "cookies": [{"name": "session", "value": "a"}],
                    "origins": [
                        {
                            "origin": "https://app.example",
                            "localStorage": [{"name": "token", "value": "abc"}],
                        }
                    ],
                }
            )
            self.assertNotIn("stale", {cookie["name"] for cookie in inner.cookies})
            self.assertEqual(inner.cookies, [{"name": "session", "value": "a"}])
            self.assertEqual(inner.storage.get("https://app.example"), {"token": "abc"})
            self.assertNotIn("https://stale.example", inner.storage)
            await borrowed.close()
            self.assertEqual(inner.cookies, [])
            self.assertEqual(inner.storage, {})

            second = await browser.new_context(
                storage_state={"cookies": [{"name": "other", "value": "b"}]}
            )
            self.assertEqual(inner.cookies, [{"name": "other", "value": "b"}])
            await second.close()

        asyncio.run(run())

    def test_video_options_relaunch_persistent_context(self) -> None:
        launched: list[tuple[str, dict[str, object]]] = []

        class FakeRecordingContext:
            browser = None

            async def close(self) -> None:
                return None

        class FakeChromium:
            async def launch_persistent_context(
                self, path: str, **options: object
            ) -> FakeRecordingContext:
                launched.append((path, options))
                return FakeRecordingContext()

        class FakePlaywright:
            chromium = FakeChromium()

        class FakeInner:
            browser = None

        async def run() -> None:
            browser = PersistentBrowser(
                FakeInner(),
                no_viewport=True,
                omit_user_agent=True,
                playwright=FakePlaywright(),
                launch_options={"headless": True, "channel": "chrome"},
            )
            borrowed = await browser.new_context(
                user_agent="should-drop",
                record_video_dir="/tmp/vipercapture-video",
                record_video_size={"width": 1280, "height": 720},
                storage_state={"cookies": [{"name": "sid"}], "origins": []},
            )
            self.assertTrue(borrowed._owns_context)
            self.assertEqual(len(launched), 1)
            _path, options = launched[0]
            self.assertEqual(options["record_video_dir"], "/tmp/vipercapture-video")
            self.assertEqual(
                options["record_video_size"], {"width": 1280, "height": 720}
            )
            self.assertEqual(options["storage_state"]["cookies"], [{"name": "sid"}])
            self.assertNotIn("user_agent", options)
            await borrowed.close()

        asyncio.run(run())

    def test_video_options_use_browser_new_context_when_available(self) -> None:
        created: list[dict[str, object]] = []

        class FakeRecordingContext:
            async def close(self) -> None:
                return None

        class FakeBrowser:
            def is_connected(self) -> bool:
                return True

            async def new_context(self, **kwargs: object) -> FakeRecordingContext:
                created.append(kwargs)
                return FakeRecordingContext()

        class FakeInner:
            browser = FakeBrowser()

        async def run() -> None:
            wrapper = PersistentBrowser(
                FakeInner(), no_viewport=True, omit_user_agent=True
            )
            borrowed = await wrapper.new_context(
                record_video_dir="/tmp/videos",
                record_video_size={"width": 800, "height": 600},
            )
            self.assertTrue(borrowed._owns_context)
            self.assertEqual(created[0]["record_video_dir"], "/tmp/videos")
            await borrowed.close()

        asyncio.run(run())

    def test_render_lock_released_after_setup_cancellation(self) -> None:
        class FakeInner:
            browser = None

        async def cancel_reset(_context: object) -> None:
            raise asyncio.CancelledError()

        async def run() -> None:
            browser = PersistentBrowser(
                FakeInner(), no_viewport=True, omit_user_agent=True
            )
            with mock.patch(
                "vipercapture.browser_launch.reset_persistent_browser_state",
                side_effect=cancel_reset,
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await browser.new_context()
            self.assertFalse(browser._render_lock.locked())

            borrowed = await browser.new_context()
            self.assertTrue(browser._render_lock.locked())
            await borrowed.close()
            self.assertFalse(browser._render_lock.locked())

        asyncio.run(run())

    def test_render_lock_released_after_setup_exception(self) -> None:
        class FakeInner:
            browser = None

        async def fail_apply(_context: object, _storage: object) -> None:
            raise RuntimeError("apply failed")

        async def run() -> None:
            browser = PersistentBrowser(
                FakeInner(), no_viewport=True, omit_user_agent=True
            )
            with mock.patch(
                "vipercapture.browser_launch.apply_persistent_storage_state",
                side_effect=fail_apply,
            ):
                with self.assertRaises(RuntimeError):
                    await browser.new_context(storage_state={"cookies": []})
            self.assertFalse(browser._render_lock.locked())

        asyncio.run(run())

    def test_recording_context_forwards_filtered_settings(self) -> None:
        created: list[dict[str, object]] = []

        class FakeRecordingContext:
            async def close(self) -> None:
                return None

        class FakeBrowser:
            def is_connected(self) -> bool:
                return True

            async def new_context(self, **kwargs: object) -> FakeRecordingContext:
                created.append(kwargs)
                return FakeRecordingContext()

        class FakeInner:
            browser = FakeBrowser()

        requested = {
            "record_video_dir": "/tmp/videos",
            "record_video_size": {"width": 800, "height": 600},
            "viewport": {"width": 390, "height": 844},
            "screen": {"width": 390, "height": 844},
            "device_scale_factor": 3,
            "locale": "fr-FR",
            "timezone_id": "Europe/Paris",
            "java_script_enabled": False,
            "geolocation": {"latitude": 48.8, "longitude": 2.3},
            "proxy": {"server": "http://proxy.example:8080"},
            "bypass_csp": True,
            "ignore_https_errors": True,
            "service_workers": "block",
            "storage_state": {"cookies": [{"name": "sid"}]},
        }

        async def run() -> None:
            wrapper = PersistentBrowser(
                FakeInner(), no_viewport=False, omit_user_agent=False
            )
            borrowed = await wrapper.new_context(**requested)
            self.assertTrue(borrowed._owns_context)
            self.assertEqual(created[0]["viewport"], requested["viewport"])
            self.assertEqual(created[0]["locale"], "fr-FR")
            self.assertEqual(created[0]["timezone_id"], "Europe/Paris")
            self.assertFalse(created[0]["java_script_enabled"])
            self.assertEqual(created[0]["geolocation"], requested["geolocation"])
            self.assertEqual(created[0]["proxy"], requested["proxy"])
            self.assertTrue(created[0]["bypass_csp"])
            self.assertTrue(created[0]["ignore_https_errors"])
            self.assertEqual(created[0]["service_workers"], "block")
            self.assertEqual(created[0]["storage_state"], requested["storage_state"])
            self.assertEqual(created[0]["record_video_dir"], "/tmp/videos")
            await borrowed.close()

        asyncio.run(run())

    def test_recording_persistent_launch_forwards_filtered_settings(self) -> None:
        launched: list[dict[str, object]] = []

        class FakeRecordingContext:
            browser = None

            async def close(self) -> None:
                return None

        class FakeChromium:
            async def launch_persistent_context(
                self, path: str, **options: object
            ) -> FakeRecordingContext:
                launched.append(options)
                return FakeRecordingContext()

        class FakePlaywright:
            chromium = FakeChromium()

        class FakeInner:
            browser = None

        async def run() -> None:
            browser = PersistentBrowser(
                FakeInner(),
                no_viewport=False,
                omit_user_agent=False,
                playwright=FakePlaywright(),
                launch_options={"headless": True, "channel": "chrome"},
            )
            borrowed = await browser.new_context(
                record_video_dir="/tmp/vipercapture-video",
                viewport={"width": 1280, "height": 720},
                locale="de-DE",
                timezone_id="Europe/Berlin",
                java_script_enabled=True,
                bypass_csp=True,
                ignore_https_errors=False,
                service_workers="block",
            )
            self.assertEqual(len(launched), 1)
            options = launched[0]
            self.assertEqual(options["viewport"], {"width": 1280, "height": 720})
            self.assertEqual(options["locale"], "de-DE")
            self.assertEqual(options["timezone_id"], "Europe/Berlin")
            self.assertTrue(options["java_script_enabled"])
            self.assertTrue(options["bypass_csp"])
            self.assertFalse(options["ignore_https_errors"])
            self.assertEqual(options["service_workers"], "block")
            self.assertEqual(options["channel"], "chrome")
            await borrowed.close()

        asyncio.run(run())

    def test_failed_recording_launch_removes_scratch_profile(self) -> None:
        leftover: list[str] = []

        class FakeChromium:
            async def launch_persistent_context(
                self, path: str, **_options: object
            ) -> None:
                leftover.append(path)
                raise RuntimeError("chrome refused")

        class FakePlaywright:
            chromium = FakeChromium()

        class FakeInner:
            browser = None

        async def run() -> None:
            browser = PersistentBrowser(
                FakeInner(),
                no_viewport=True,
                omit_user_agent=True,
                playwright=FakePlaywright(),
                launch_options={"headless": True},
            )
            with self.assertRaises(RuntimeError):
                await browser.new_context(record_video_dir="/tmp/videos")
            self.assertEqual(len(leftover), 1)
            self.assertFalse(Path(leftover[0]).exists())

        asyncio.run(run())

    def test_cancelled_recording_launch_removes_scratch_profile(self) -> None:
        leftover: list[str] = []

        class FakeChromium:
            async def launch_persistent_context(
                self, path: str, **_options: object
            ) -> None:
                leftover.append(path)
                raise asyncio.CancelledError()

        class FakePlaywright:
            chromium = FakeChromium()

        class FakeInner:
            browser = None

        async def run() -> None:
            browser = PersistentBrowser(
                FakeInner(),
                no_viewport=True,
                omit_user_agent=True,
                playwright=FakePlaywright(),
                launch_options={"headless": True},
            )
            with self.assertRaises(asyncio.CancelledError):
                await browser.new_context(record_video_dir="/tmp/videos")
            self.assertEqual(len(leftover), 1)
            self.assertFalse(Path(leftover[0]).exists())

        asyncio.run(run())

    def test_persistent_slots_are_reused_after_release(self) -> None:
        self.assertEqual(next_persistent_slot(), 0)
        self.assertEqual(next_persistent_slot(), 1)
        self.assertEqual(used_persistent_slots(), {0, 1})
        release_persistent_slot(0)
        self.assertEqual(next_persistent_slot(), 0)
        self.assertEqual(used_persistent_slots(), {0, 1})

    def test_launch_reuses_released_profile_directory(self) -> None:
        launched: list[str] = []

        class FakeContext:
            browser = None

            async def close(self) -> None:
                return None

        class FakeChromium:
            async def launch_persistent_context(
                self, path: str, **_options: object
            ) -> FakeContext:
                launched.append(path)
                return FakeContext()

        class FakePlaywright:
            chromium = FakeChromium()

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.dict(
                    os.environ, {"VIPERCAPTURE_PATCHRIGHT_USER_DATA_DIR": tmp}
                ):
                    first = await launch_chromium(
                        FakePlaywright(), gpu_mode="off", persistent=True
                    )
                    self.assertEqual(first.persistent_slot, 0)
                    first_dir = first.user_data_dir
                    await first.close()
                    self.assertEqual(used_persistent_slots(), set())
                    recycled = await launch_chromium(
                        FakePlaywright(),
                        gpu_mode="off",
                        persistent=True,
                        user_data_dir=first_dir,
                    )
                    self.assertEqual(recycled.persistent_slot, 0)
                    self.assertEqual(recycled.user_data_dir, first_dir)
                    await recycled.close()
                    reused = await launch_chromium(
                        FakePlaywright(), gpu_mode="off", persistent=True
                    )
                    self.assertEqual(reused.persistent_slot, 0)
                    await reused.close()

        asyncio.run(run())
        self.assertEqual(slot_from_user_data_dir(Path("/data/slot-7")), 7)

    def test_failed_persistent_launch_releases_slot(self) -> None:
        class FakeChromium:
            async def launch_persistent_context(self, _path: str, **_options: object) -> None:
                raise RuntimeError("launch failed")

        class FakePlaywright:
            chromium = FakeChromium()

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.dict(
                    os.environ, {"VIPERCAPTURE_PATCHRIGHT_USER_DATA_DIR": tmp}
                ):
                    with self.assertRaises(RuntimeError):
                        await launch_chromium(
                            FakePlaywright(), gpu_mode="off", persistent=True
                        )
            self.assertEqual(used_persistent_slots(), set())

        asyncio.run(run())

    def test_apply_and_reset_storage_helpers(self) -> None:
        class FakePage:
            def __init__(self, owner: "FakeInner") -> None:
                self.owner = owner

            async def route(self, _pattern: str, _handler: object) -> None:
                return None

            async def goto(self, origin: str, **_kwargs: object) -> None:
                self.owner.current = origin

            async def evaluate(self, _script: str, payload: dict[str, object]) -> None:
                origin = getattr(self.owner, "current", "")
                if payload.get("clear"):
                    self.owner.storage.pop(origin, None)
                else:
                    self.owner.storage[origin] = list(payload.get("items") or [])

            async def close(self) -> None:
                return None

        class FakeInner:
            def __init__(self) -> None:
                self.cookies: list[object] = [{"name": "keep"}]
                self.storage: dict[str, object] = {
                    "https://old.example": [{"name": "x", "value": "1"}]
                }
                self.current = ""

            async def storage_state(self) -> dict[str, object]:
                return {
                    "cookies": list(self.cookies),
                    "origins": [
                        {"origin": origin, "localStorage": items}
                        for origin, items in self.storage.items()
                    ],
                }

            async def clear_cookies(self) -> None:
                self.cookies.clear()

            async def add_cookies(self, cookies: list[object]) -> None:
                self.cookies.extend(cookies)

            async def new_page(self) -> FakePage:
                return FakePage(self)

        async def run() -> None:
            inner = FakeInner()
            await reset_persistent_browser_state(inner)
            self.assertEqual(inner.cookies, [])
            self.assertEqual(inner.storage, {})
            await apply_persistent_storage_state(
                inner,
                {
                    "cookies": [{"name": "sid"}],
                    "origins": [
                        {
                            "origin": "https://app.example",
                            "localStorage": [{"name": "token", "value": "z"}],
                        }
                    ],
                },
            )
            self.assertEqual(inner.cookies, [{"name": "sid"}])
            self.assertEqual(
                inner.storage["https://app.example"],
                [{"name": "token", "value": "z"}],
            )

        asyncio.run(run())

    def test_video_recording_options_extracts_record_keys(self) -> None:
        self.assertEqual(
            video_recording_options(
                {
                    "record_video_dir": "/tmp/v",
                    "record_video_size": {"width": 1, "height": 1},
                    "locale": "en-US",
                    "record_video_dir_unused": None,
                }
            ),
            {
                "record_video_dir": "/tmp/v",
                "record_video_size": {"width": 1, "height": 1},
            },
        )

    def test_display_available_reads_env(self) -> None:
        with mock.patch.dict(os.environ, {"DISPLAY": ":0"}):
            self.assertTrue(display_available())


if __name__ == "__main__":
    unittest.main()
