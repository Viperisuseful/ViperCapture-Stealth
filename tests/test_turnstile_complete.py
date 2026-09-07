from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vipercapture.captcha import (  # noqa: E402
    CHALLENGE_PHRASES,
    CLICK_BACKOFF_MS,
    CLICK_TIMEOUT_MS,
    DETECT_CHALLENGE_SCRIPT,
    MAX_CLICK_ATTEMPTS,
    PASS_PHRASES,
    TURNSTILE_CHECKBOX_SELECTORS,
    auto_pass_wait_ms,
    challenge_cleared,
    click_retry_wait_ms,
    click_turnstile_widget,
    complete_cloudflare_turnstile,
    detect_challenge,
    handle_challenge,
    is_cloudflare_challenge,
    merge_challenge_signals,
    post_click_wait_ms,
)
from vipercapture.render_errors import RenderError  # noqa: E402

CF_BLOCKING = {
    "provider": "cloudflare",
    "kind": "blocking_interstitial",
    "confidence": 0.98,
    "signals": ["challenge_form"],
}
CF_EMBEDDED = {
    "provider": "cloudflare",
    "kind": "embedded_widget",
    "confidence": 0.72,
    "signals": ["provider_widget"],
}
UNKNOWN_403 = {
    "provider": "unknown",
    "kind": "access_denied",
    "confidence": 0.88,
    "signals": ["main_response_403", "challenge_url"],
}


class FakeLocator:
    def __init__(self, clicks: list[str], name: str):
        self._clicks = clicks
        self._name = name
        self.first = self

    async def click(self, timeout: int = 0) -> None:
        self._clicks.append(self._name)


class FakeFrame:
    def __init__(self, clicks: list[str], url: str = "", name: str = ""):
        self.url = url
        self.name = name
        self._clicks = clicks

    def get_by_role(self, role: str) -> FakeLocator:
        return FakeLocator(self._clicks, f"role:{role}")

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self._clicks, f"frame:{selector}")


class FakePage:
    def __init__(
        self,
        results: list[dict[str, object] | None],
        *,
        clicks: list[str] | None = None,
        frame_url: str = "https://challenges.cloudflare.com/turnstile",
        frame_name: str = "",
        exhausted: dict[str, object] | None = None,
        clear_after_clicks: int | None = None,
    ):
        self._results = list(results)
        self.clicks = clicks if clicks is not None else []
        self._frame_url = frame_url
        self._frame_name = frame_name
        self._exhausted = exhausted
        self._clear_after_clicks = clear_after_clicks
        self._passed = False

    @property
    def frames(self) -> list[FakeFrame]:
        if self._passed or not self._frame_url:
            return []
        return [FakeFrame(self.clicks, self._frame_url, self._frame_name)]

    async def evaluate(self, _script: str, _payload: dict[str, object]) -> dict[str, object] | None:
        if self._clear_after_clicks is not None:
            checkbox_clicks = [item for item in self.clicks if "checkbox" in item]
            if len(checkbox_clicks) >= self._clear_after_clicks:
                self._passed = True
                return None
            return self._results[0] if self._results else (self._exhausted or CF_BLOCKING)
        if not self._results:
            self._passed = self._exhausted is None
            return self._exhausted
        result = self._results.pop(0)
        self._passed = result is None or str((result or {}).get("kind") or "") == "passed"
        return result

    def frame_locator(self, selector: str) -> FakeFrame:
        self.clicks.append(f"frame_locator:{selector}")
        return FakeFrame(self.clicks)

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self.clicks, f"host:{selector}")


def _run(coro):
    return asyncio.run(coro)


class TurnstileCompleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._now = 1_000.0

        def tick() -> float:
            self._now += 0.01
            return self._now

        self.settle = mock.patch("vipercapture.captcha.POST_PASS_SETTLE_MS", 0)
        self.auto = mock.patch("vipercapture.captcha.AUTO_PASS_POLL_S", 0)
        self.sleep = mock.patch("vipercapture.captcha.asyncio.sleep", new=mock.AsyncMock())
        self.clock = mock.patch("vipercapture.captcha.time.monotonic", side_effect=tick)
        self.settle.start()
        self.auto.start()
        self.sleep.start()
        self.clock.start()
        self.addCleanup(self.settle.stop)
        self.addCleanup(self.auto.stop)
        self.addCleanup(self.sleep.stop)
        self.addCleanup(self.clock.stop)

    def test_click_uses_frame_checkbox_locator(self) -> None:
        page = FakePage([CF_BLOCKING])
        clicked = _run(click_turnstile_widget(page))
        self.assertTrue(clicked)
        self.assertTrue(any("checkbox" in item for item in page.clicks))

    def test_managed_auto_pass_skips_error(self) -> None:
        page = FakePage([None])
        self.assertTrue(
            _run(
                complete_cloudflare_turnstile(
                    page, timeout_ms=5_000, navigation_status=403
                )
            )
        )

    def test_auto_pass_stop_when_is_invoked_without_args(self) -> None:
        """wait_for_challenge_clear calls stop_when() with no args."""
        page = FakePage([CF_BLOCKING])
        invoked: list[bool] = []

        async def fake_wait(
            _page: object,
            *,
            timeout_ms: int,
            navigation_status: int | None,
            poll_s: float = 0,
            stop_when=None,
        ) -> bool:
            if stop_when is not None:
                invoked.append(await stop_when())
            return False

        with mock.patch("vipercapture.captcha.AUTO_PASS_POLL_S", 8.0):
            with mock.patch(
                "vipercapture.captcha.wait_for_challenge_clear",
                new=fake_wait,
            ):
                _run(complete_cloudflare_turnstile(page, timeout_ms=20_000))
        self.assertEqual(len(invoked), 1)

    def test_embedded_widget_is_not_treated_as_cleared(self) -> None:
        page = FakePage([CF_EMBEDDED], clear_after_clicks=1)
        self.assertTrue(
            _run(complete_cloudflare_turnstile(page, timeout_ms=5_000))
        )
        self.assertTrue(any("checkbox" in item for item in page.clicks))

    def test_evaluate_unknown_with_turnstile_frame_still_clicks(self) -> None:
        page = FakePage(
            [UNKNOWN_403],
            frame_url="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/x",
            frame_name="cf-chl-widget-test",
            clear_after_clicks=1,
        )
        _run(
            handle_challenge(
                page,
                navigation_status=403,
                action="error",
                handler=None,
                solver=None,
                timeout_ms=5_000,
            )
        )
        self.assertTrue(any("checkbox" in item for item in page.clicks))

    def test_retry_click_when_widget_still_present(self) -> None:
        page = FakePage([], clear_after_clicks=2)
        self.assertTrue(
            _run(complete_cloudflare_turnstile(page, timeout_ms=8_000))
        )
        checkbox_clicks = [item for item in page.clicks if "checkbox" in item]
        self.assertGreaterEqual(len(checkbox_clicks), 2)

    def test_interactive_challenge_remains_honest_failure(self) -> None:
        page = FakePage([], exhausted=CF_BLOCKING)
        self.assertFalse(
            _run(complete_cloudflare_turnstile(page, timeout_ms=3_000))
        )

    def test_handle_challenge_completes_cloudflare_without_solver(self) -> None:
        page = FakePage([CF_BLOCKING], clear_after_clicks=1)
        _run(
            handle_challenge(
                page,
                navigation_status=403,
                action="error",
                handler=None,
                solver=None,
                timeout_ms=5_000,
            )
        )

    def test_handle_challenge_errors_when_turnstile_still_blocking(self) -> None:
        page = FakePage([CF_BLOCKING], exhausted=CF_BLOCKING)

        async def run() -> None:
            await handle_challenge(
                page,
                navigation_status=403,
                action="error",
                handler=None,
                solver=None,
                timeout_ms=3_000,
            )

        with self.assertRaises(RenderError) as details:
            _run(run())
        self.assertEqual(details.exception.code, "captcha_detected")

    def test_capture_action_keeps_visible_challenge(self) -> None:
        page = FakePage([CF_BLOCKING], exhausted=CF_BLOCKING)
        _run(
            handle_challenge(
                page,
                navigation_status=403,
                action="capture",
                handler=None,
                solver=None,
                timeout_ms=3_000,
            )
        )

    def test_click_disabled_skips_locator_interaction(self) -> None:
        page = FakePage([CF_BLOCKING])
        with mock.patch.dict(os.environ, {"VIPERCAPTURE_TURNSTILE_CLICK": "0"}):
            with self.assertRaises(RenderError):
                _run(
                    handle_challenge(
                        page,
                        navigation_status=403,
                        action="error",
                        handler=None,
                        solver=None,
                        timeout_ms=1_000,
                    )
                )
        self.assertFalse(any("checkbox" in item for item in page.clicks))

    def test_module_does_not_import_solver_services(self) -> None:
        source = Path("vipercapture/captcha.py").read_text(encoding="utf-8")
        lowered = source.lower()
        for needle in ("2captcha", "anti-captcha", "anticaptcha", "capsolver", "twocaptcha"):
            self.assertNotIn(needle, lowered)

    def test_locator_attempts_are_bounded_by_timeout(self) -> None:
        self.clock.stop()
        self.sleep.stop()
        timeouts: list[int] = []

        class FailLocator:
            def __init__(self) -> None:
                self.first = self

            async def click(self, timeout: int = 0) -> None:
                timeouts.append(timeout)
                await asyncio.sleep(timeout / 1_000)
                raise TimeoutError("checkbox not reachable")

        class FailFrame:
            url = "https://challenges.cloudflare.com/turnstile"

            def get_by_role(self, _role: str) -> FailLocator:
                return FailLocator()

            def locator(self, _selector: str) -> FailLocator:
                return FailLocator()

        class FailPage:
            frames = [FailFrame()]

            def frame_locator(self, _selector: str) -> FailFrame:
                return FailFrame()

            def locator(self, _selector: str) -> FailLocator:
                return FailLocator()

        started = time.monotonic()
        clicked = _run(click_turnstile_widget(FailPage(), timeout_ms=180))
        elapsed = time.monotonic() - started
        self.assertFalse(clicked)
        self.assertTrue(timeouts)
        self.assertTrue(all(item <= 180 for item in timeouts))
        self.assertLess(sum(timeouts), 4 * 6 * CLICK_TIMEOUT_MS)
        self.assertLess(elapsed, 1.5)

    def test_complete_turnstile_does_not_exceed_configured_timeout(self) -> None:
        self.clock.stop()
        self.sleep.stop()
        timeouts: list[int] = []

        class FailLocator:
            def __init__(self) -> None:
                self.first = self

            async def click(self, timeout: int = 0) -> None:
                timeouts.append(timeout)
                await asyncio.sleep(timeout / 1_000)
                raise TimeoutError("checkbox not reachable")

        class FailFrame:
            url = "https://challenges.cloudflare.com/turnstile"

            def get_by_role(self, _role: str) -> FailLocator:
                return FailLocator()

            def locator(self, _selector: str) -> FailLocator:
                return FailLocator()

        class FailPage:
            frames = [FailFrame()]

            async def evaluate(
                self, _script: str, _payload: dict[str, object]
            ) -> dict[str, object]:
                return CF_BLOCKING

            def frame_locator(self, _selector: str) -> FailFrame:
                return FailFrame()

            def locator(self, _selector: str) -> FailLocator:
                return FailLocator()

        started = time.monotonic()
        cleared = _run(complete_cloudflare_turnstile(FailPage(), timeout_ms=1_000))
        elapsed = time.monotonic() - started
        self.assertFalse(cleared)
        self.assertTrue(timeouts)
        self.assertTrue(all(item <= 1_000 for item in timeouts))
        self.assertLess(sum(timeouts), 4 * 6 * CLICK_TIMEOUT_MS)
        self.assertLess(elapsed, 2.5)


class TurnstileDetectionAndBudgetTests(unittest.TestCase):
    def test_detector_script_includes_managed_challenge_copy(self) -> None:
        script = DETECT_CHALLENGE_SCRIPT.lower()
        for phrase in CHALLENGE_PHRASES:
            self.assertIn(phrase, script)
        self.assertIn("just a moment", script)
        self.assertIn("_cf_chl_opt", script)
        self.assertIn("cf-chl-widget", script)
        self.assertIn("cdn-cgi", script)
        self.assertIn("you bypassed", script)
        self.assertIn("cf-turnstile-response", script)
        for phrase in PASS_PHRASES:
            self.assertIn(phrase, script)

    def test_checkbox_selectors_do_not_include_body(self) -> None:
        self.assertNotIn("body", TURNSTILE_CHECKBOX_SELECTORS)

    def test_embedded_widget_is_not_cleared_without_token(self) -> None:
        self.assertFalse(challenge_cleared(CF_EMBEDDED))
        self.assertFalse(
            challenge_cleared(
                {
                    "provider": "unknown",
                    "kind": "access_denied",
                    "signals": ["main_response_403", "turnstile_frame"],
                }
            )
        )
        self.assertTrue(
            challenge_cleared(
                {
                    "provider": "cloudflare",
                    "kind": "passed",
                    "signals": ["turnstile_token"],
                }
            )
        )
        self.assertTrue(
            challenge_cleared(
                {
                    "provider": "cloudflare",
                    "kind": "access_denied",
                    "signals": ["main_response_403", "bypass_copy"],
                }
            )
        )
        self.assertTrue(challenge_cleared(None))

    def test_unknown_403_plus_locator_frame_is_cloudflare(self) -> None:
        merged = merge_challenge_signals(
            UNKNOWN_403,
            {
                "provider": "cloudflare",
                "kind": "blocking_interstitial",
                "confidence": 0.9,
                "signals": ["locator_widget", "turnstile_frame"],
            },
        )
        self.assertEqual(merged["provider"], "cloudflare")
        self.assertTrue(is_cloudflare_challenge(merged))
        self.assertFalse(challenge_cleared(merged))

    def test_passed_evaluate_ignores_stale_403_and_leftover_frames(self) -> None:
        merged = merge_challenge_signals(
            {
                "provider": "cloudflare",
                "kind": "passed",
                "signals": ["bypass_copy", "main_response_403"],
            },
            {
                "provider": "cloudflare",
                "kind": "blocking_interstitial",
                "signals": ["turnstile_frame"],
            },
        )
        self.assertIsNone(merged)

    def test_detect_ignores_stale_403_when_evaluate_reports_passed(self) -> None:
        class PassedPage:
            frames = [
                FakeFrame([], "https://challenges.cloudflare.com/turnstile", "cf-chl-widget-x")
            ]

            async def evaluate(
                self, _script: str, payload: dict[str, object]
            ) -> dict[str, object]:
                self.status = payload.get("status")
                return {
                    "provider": "cloudflare",
                    "kind": "passed",
                    "signals": ["bypass_copy", "main_response_403"],
                }

            def locator(self, selector: str) -> FakeLocator:
                return FakeLocator([], selector)

        page = PassedPage()
        self.assertIsNone(_run(detect_challenge(page, 403)))
        self.assertEqual(page.status, 403)

    def test_merge_locator_blocking_overrides_embedded_widget(self) -> None:
        merged = merge_challenge_signals(
            {
                "provider": "unknown",
                "kind": "embedded_widget",
                "confidence": 0.72,
                "signals": ["provider_widget"],
            },
            {
                "provider": "cloudflare",
                "kind": "blocking_interstitial",
                "confidence": 0.9,
                "signals": ["locator_widget", "locator_challenge_form"],
            },
        )
        self.assertEqual(merged["provider"], "cloudflare")
        self.assertEqual(merged["kind"], "blocking_interstitial")
        self.assertIn("locator_challenge_form", merged["signals"])

    def test_locator_only_hit_is_returned(self) -> None:
        merged = merge_challenge_signals(
            None,
            {
                "provider": "cloudflare",
                "kind": "embedded_widget",
                "confidence": 0.72,
                "signals": ["locator_widget"],
            },
        )
        self.assertEqual(merged["kind"], "embedded_widget")

    def test_auto_pass_and_retry_budgeting(self) -> None:
        self.assertEqual(auto_pass_wait_ms(1_000), 0)
        self.assertEqual(auto_pass_wait_ms(20_000), 8_000)
        self.assertEqual(click_retry_wait_ms(0, 5_000), CLICK_BACKOFF_MS[0])
        self.assertEqual(click_retry_wait_ms(1, 5_000), CLICK_BACKOFF_MS[1])
        self.assertEqual(click_retry_wait_ms(2, 5_000), CLICK_BACKOFF_MS[2])
        self.assertEqual(click_retry_wait_ms(0, 100), 100)
        self.assertEqual(click_retry_wait_ms(0, 0), 0)
        self.assertEqual(post_click_wait_ms(6_000, 2, True), 2_000)
        self.assertEqual(post_click_wait_ms(5_000, 1, False), 2_000)
        self.assertEqual(MAX_CLICK_ATTEMPTS, 3)

    def test_detect_challenge_merges_closed_shadow_locator_hit(self) -> None:
        class VisibleLocator:
            def __init__(self) -> None:
                self.first = self

            async def is_visible(self, timeout: int = 0) -> bool:
                return True

        class LocatorPage:
            frames: list = []

            async def evaluate(
                self, _script: str, _payload: dict[str, object]
            ) -> None:
                return None

            def locator(self, selector: str) -> VisibleLocator | FakeLocator:
                if selector == "#challenge-running":
                    return VisibleLocator()
                return FakeLocator([], selector)

        challenge = _run(detect_challenge(LocatorPage(), 403))
        self.assertIsNotNone(challenge)
        self.assertEqual(challenge["provider"], "cloudflare")
        self.assertEqual(challenge["kind"], "blocking_interstitial")
        self.assertIn("locator_challenge_form", challenge["signals"])


class HumanClickAndRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._now = 1_000.0

        def tick() -> float:
            self._now += 0.01
            return self._now

        self.settle = mock.patch("vipercapture.captcha.POST_PASS_SETTLE_MS", 0)
        self.auto = mock.patch("vipercapture.captcha.AUTO_PASS_POLL_S", 0)
        self.sleep = mock.patch("vipercapture.captcha.asyncio.sleep", new=mock.AsyncMock())
        self.clock = mock.patch("vipercapture.captcha.time.monotonic", side_effect=tick)
        self.settle.start()
        self.auto.start()
        self.sleep.start()
        self.clock.start()
        self.addCleanup(self.settle.stop)
        self.addCleanup(self.auto.stop)
        self.addCleanup(self.sleep.stop)
        self.addCleanup(self.clock.stop)

    def test_click_uses_mousemove_then_click_when_supported(self) -> None:
        events: list[object] = []

        class FakeMouse:
            async def move(self, x: float, y: float, steps: int = 1) -> None:
                events.append(("move", x, y, steps))

            async def click(self, x: float, y: float, delay: float = 0) -> None:
                events.append(("click", x, y, delay))

        class MouseLocator:
            def __init__(self, page: object) -> None:
                self.page = page
                self.first = self

            async def bounding_box(self) -> dict[str, float]:
                return {"x": 10.0, "y": 20.0, "width": 40.0, "height": 40.0}

            async def click(self, timeout: int = 0) -> None:
                events.append("locator-click")

        class ShadowPage:
            def __init__(self) -> None:
                self.mouse = FakeMouse()
                self.frames: list = []

            def frame_locator(self, _selector: str) -> object:
                return object()

            def get_by_role(self, role: str) -> MouseLocator:
                self.assert_role = role
                return MouseLocator(self)

            def locator(self, _selector: str) -> FakeLocator:
                return FakeLocator(events, "unused")  # type: ignore[arg-type]

        page = ShadowPage()
        clicked = _run(click_turnstile_widget(page))
        self.assertTrue(clicked)
        self.assertEqual(getattr(page, "assert_role", ""), "checkbox")
        self.assertTrue(any(item[0] == "move" and item[3] >= 2 for item in events if isinstance(item, tuple)))
        self.assertTrue(any(item[0] == "click" for item in events if isinstance(item, tuple)))
        self.assertNotIn("locator-click", events)

    def test_hidden_locators_are_skipped(self) -> None:
        clicks: list[str] = []

        class HiddenLocator:
            def __init__(self) -> None:
                self.first = self

            async def is_visible(self, timeout: int = 0) -> bool:
                return False

            async def click(self, timeout: int = 0) -> None:
                clicks.append("hidden")

        class VisibleLocator:
            def __init__(self) -> None:
                self.first = self

            async def is_visible(self, timeout: int = 0) -> bool:
                return True

            async def click(self, timeout: int = 0) -> None:
                clicks.append("visible")

        class MixedFrame:
            url = "https://challenges.cloudflare.com/turnstile"

            def get_by_role(self, _role: str) -> HiddenLocator:
                return HiddenLocator()

            def locator(self, selector: str) -> HiddenLocator | VisibleLocator:
                if selector == "[role='checkbox']":
                    return VisibleLocator()
                return HiddenLocator()

        class MixedPage:
            frames = [MixedFrame()]

            def frame_locator(self, _selector: str) -> MixedFrame:
                return MixedFrame()

            def locator(self, _selector: str) -> HiddenLocator:
                return HiddenLocator()

        self.assertTrue(_run(click_turnstile_widget(MixedPage())))
        self.assertEqual(clicks, ["visible"])
        self.assertNotIn("hidden", clicks)

    def test_retry_sleeps_increase_with_backoff(self) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        page = FakePage([], clear_after_clicks=3)
        with mock.patch("vipercapture.captcha.asyncio.sleep", new=fake_sleep):
            cleared = _run(complete_cloudflare_turnstile(page, timeout_ms=20_000))
        self.assertTrue(cleared)
        checkbox_clicks = [item for item in page.clicks if "checkbox" in item]
        self.assertGreaterEqual(len(checkbox_clicks), 3)
        click_backoff = [
            item
            for item in sleeps
            if item in {ms / 1_000 for ms in CLICK_BACKOFF_MS}
        ]
        self.assertTrue(click_backoff)
        self.assertEqual(click_backoff, sorted(click_backoff))


if __name__ == "__main__":
    unittest.main()
