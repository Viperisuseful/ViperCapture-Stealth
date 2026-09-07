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
    browser_channel,
    chromium_launch_args,
    chromium_launch_options,
    display_available,
    filter_persistent_context_options,
    headed_chrome_hint,
    headless_enabled,
    no_viewport_enabled,
    omit_custom_user_agent,
    persistent_context_enabled,
    persistent_launch_options,
    persistent_user_data_dir,
    sweetspot_enabled,
    swiftshader_enabled,
    turnstile_click_enabled,
    turnstile_timeout_ms,
)


class EnvKnobTests(unittest.TestCase):
    def test_persistent_and_sweetspot_default_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in (
                "VIPERCAPTURE_PATCHRIGHT_PERSISTENT",
                "VIPERCAPTURE_PATCHRIGHT_SWEETSPOT",
                "VIPERCAPTURE_BROWSER_CHANNEL",
                "VIPERCAPTURE_HEADLESS",
                "VIPERCAPTURE_PATCHRIGHT_NO_VIEWPORT",
                "VIPERCAPTURE_SWIFTSHADER",
            ):
                os.environ.pop(name, None)
            self.assertFalse(persistent_context_enabled())
            self.assertFalse(sweetspot_enabled())
            self.assertEqual(browser_channel(), "chromium")
            self.assertTrue(headless_enabled())
            self.assertFalse(no_viewport_enabled())
            self.assertFalse(swiftshader_enabled())
            self.assertTrue(turnstile_click_enabled())
            self.assertEqual(turnstile_timeout_ms(), 20_000)

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
            os.environ.pop("VIPERCAPTURE_SWIFTSHADER", None)
            os.environ.pop("VIPERCAPTURE_BROWSER_CHANNEL", None)
            os.environ.pop("VIPERCAPTURE_HEADLESS", None)
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
        self.assertIn("VIPERCAPTURE_BROWSER_CHANNEL=chrome", hint or "")
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

    def test_display_available_reads_env(self) -> None:
        with mock.patch.dict(os.environ, {"DISPLAY": ":0"}):
            self.assertTrue(display_available())


if __name__ == "__main__":
    unittest.main()
