from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vipercapture.captcha import (  # noqa: E402
    click_turnstile_widget,
    complete_cloudflare_turnstile,
    handle_challenge,
)
from vipercapture.render_errors import RenderError  # noqa: E402

CF_BLOCKING = {
    "provider": "cloudflare",
    "kind": "blocking_interstitial",
    "confidence": 0.98,
    "signals": ["challenge_form"],
}


class FakeLocator:
    def __init__(self, clicks: list[str], name: str):
        self._clicks = clicks
        self._name = name
        self.first = self

    async def click(self, timeout: int = 0) -> None:
        self._clicks.append(self._name)


class FakeFrame:
    def __init__(self, clicks: list[str], url: str = ""):
        self.url = url
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
        exhausted: dict[str, object] | None = None,
        clear_after_clicks: int | None = None,
    ):
        self._results = list(results)
        self.clicks = clicks if clicks is not None else []
        self.frames = [FakeFrame(self.clicks, frame_url)]
        self._exhausted = exhausted
        self._clear_after_clicks = clear_after_clicks

    async def evaluate(self, _script: str, _payload: dict[str, object]) -> dict[str, object] | None:
        if self._clear_after_clicks is not None:
            checkbox_clicks = [item for item in self.clicks if "checkbox" in item]
            if len(checkbox_clicks) >= self._clear_after_clicks:
                return None
            return CF_BLOCKING
        if not self._results:
            return self._exhausted
        return self._results.pop(0)

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
            self._now += 1.0
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

    def test_click_then_clear_counts_as_complete(self) -> None:
        page = FakePage([], clear_after_clicks=1)
        self.assertTrue(
            _run(complete_cloudflare_turnstile(page, timeout_ms=5_000))
        )
        self.assertTrue(page.clicks)

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


if __name__ == "__main__":
    unittest.main()
