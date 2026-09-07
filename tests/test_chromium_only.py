from __future__ import annotations

import pytest
from pydantic import ValidationError

from vipercapture.render_contract import (
    CHROMIUM_ONLY_ENGINE_MESSAGE,
    BrowserEngine,
    RenderRequest,
)


def test_default_engine_is_chromium() -> None:
    request = RenderRequest.model_validate({"html": "<h1>ready</h1>", "output": "png"})
    assert request.engine is BrowserEngine.CHROMIUM


@pytest.mark.parametrize("engine", ["firefox", "webkit"])
def test_firefox_and_webkit_are_rejected(engine: str) -> None:
    with pytest.raises(ValidationError) as exc:
        RenderRequest.model_validate(
            {
                "html": "<h1>ready</h1>",
                "output": "png",
                "engine": engine,
            }
        )
    assert CHROMIUM_ONLY_ENGINE_MESSAGE in str(exc.value)


def test_requirements_drop_playwright_runtime() -> None:
    text = open("requirements.txt", encoding="utf-8").read()
    assert "patchright" in text
    assert "playwright-stealth" not in text
    assert "playwright>=" not in text
    assert "playwright==" not in text
