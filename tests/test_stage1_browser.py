import json
import subprocess

import pytest

from stage1_2gis.browser import BrowserDisplayError, PlaywrightBrowserAdapter


class FakeTracing:
    def stop(self, *, path):
        path.write_bytes(b"trace")


class FakeContext:
    tracing = FakeTracing()


class FakeBody:
    def inner_text(self, *, timeout):
        assert timeout == 2_000
        return "Подтвердите, что вы человек"


class FakePage:
    url = "https://2gis.ru/captcha"

    def title(self):
        return "Проверка: робот или человек"

    def locator(self, selector):
        assert selector == "body"
        return FakeBody()

    def screenshot(self, *, path, full_page):
        assert full_page
        path.write_bytes(b"png")

    def content(self):
        return "<html><body>captcha</body></html>"


class FakeNoResultsBody:
    def inner_text(self, *, timeout):
        assert timeout == 2_000
        return "Ничего не нашлось, попробуйте уточнить запрос"


class FakeNoResultsPage:
    def title(self):
        return "Поиск в 2ГИС"

    def locator(self, selector):
        assert selector == "body"
        return FakeNoResultsBody()


def test_failure_artifacts_include_trace_screenshot_html_and_metadata(tmp_path):
    screenshot = tmp_path / "job-attempt.png"
    paths = PlaywrightBrowserAdapter._write_failure_artifacts(
        FakeContext(),
        FakePage(),
        tmp_path / "job-attempt.zip",
        screenshot,
        url="https://2gis.ru/search/test",
        error=TimeoutError("timed out"),
    )

    assert {path.suffix for path in paths} == {".zip", ".png", ".html", ".json"}
    metadata = json.loads(screenshot.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["requested_url"] == "https://2gis.ru/search/test"
    assert metadata["page_url"] == "https://2gis.ru/captcha"
    assert metadata["captcha_suspected"] is True
    assert metadata["error"] == "timed out"


def test_no_results_page_is_a_normal_empty_search_result():
    assert PlaywrightBrowserAdapter._page_looks_like_no_results(FakeNoResultsPage()) is True


def test_headed_preflight_rejects_missing_display(monkeypatch, tmp_path):
    monkeypatch.delenv("DISPLAY", raising=False)
    browser = PlaywrightBrowserAdapter(
        headed=True, disable_images=True, timeout_seconds=1, artifacts_dir=tmp_path,
    )

    with pytest.raises(BrowserDisplayError, match="DISPLAY is unset"):
        browser.preflight()


def test_headed_preflight_checks_the_x_server(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(
        "stage1_2gis.browser.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], returncode=1),
    )
    browser = PlaywrightBrowserAdapter(
        headed=True, disable_images=True, timeout_seconds=1, artifacts_dir=tmp_path,
    )

    with pytest.raises(BrowserDisplayError, match="DISPLAY=':99' is unavailable"):
        browser.preflight()
