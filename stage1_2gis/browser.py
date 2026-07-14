"""Browser adapters used by the Stage 1 worker."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

LOGGER = logging.getLogger(__name__)
CatalogDocumentHandler = Callable[[dict[str, Any]], None]
_ITEM_RESPONSE_PATTERN = re.compile(r"https://catalog\.api\.2gis\.[^/]+/.*/items/byid")
_ITEM_LINK_PATTERN = re.compile(r"/(firm|station)/.*\?stat=")


class BrowserError(RuntimeError):
    """Base classified browser error."""

    retryable = True
    error_code = "browser_error"


class BrowserContractError(BrowserError):
    """Raised when the 2GIS page no longer matches the expected contract."""

    retryable = False
    error_code = "browser_contract_changed"


class BrowserTimeoutError(BrowserError):
    error_code = "browser_timeout"


class BrowserCaptchaError(BrowserError):
    error_code = "captcha_detected"


_CAPTCHA_PATTERN = re.compile(
    r"captcha|капч|подтвердите.{0,30}(что вы|человек)|робот|unusual traffic|access denied",
    re.IGNORECASE,
)


class BrowserAdapter(Protocol):
    def collect_items(
        self,
        *,
        url: str,
        max_records: int,
        on_document: CatalogDocumentHandler,
        artifact_prefix: str,
    ) -> int: ...


class PlaywrightBrowserAdapter:
    """Collect 2GIS catalog payloads using a Playwright-managed Chromium."""

    def __init__(
        self,
        *,
        headed: bool,
        disable_images: bool,
        timeout_seconds: int,
        artifacts_dir: Path,
    ) -> None:
        self._headed = headed
        self._disable_images = disable_images
        self._timeout_ms = timeout_seconds * 1000
        self._artifacts_dir = artifacts_dir

    def collect_items(
        self,
        *,
        url: str,
        max_records: int,
        on_document: CatalogDocumentHandler,
        artifact_prefix: str,
    ) -> int:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        trace_path = self._artifacts_dir / f"{artifact_prefix}.zip"
        screenshot_path = self._artifacts_dir / f"{artifact_prefix}.png"
        collected_ids: set[str] = set()
        callback_errors: list[Exception] = []

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=not self._headed,
                    chromium_sandbox=True,
                )
                context = browser.new_context(
                    locale="ru-RU",
                    viewport={"width": 1280, "height": 1024},
                )
                context.tracing.start(screenshots=True, snapshots=True, sources=False)
                page = context.new_page()
                page.set_default_timeout(self._timeout_ms)

                try:
                    if self._disable_images:
                        page.route(
                            "**/*",
                            lambda route: route.abort()
                            if route.request.resource_type in {"image", "media", "font"}
                            else route.continue_(),
                        )

                    def handle_response(response) -> None:
                        if not _ITEM_RESPONSE_PATTERN.match(response.url) or not response.ok:
                            return
                        try:
                            document = response.json()
                            item_id = str(document["result"]["items"][0]["id"])
                        except (KeyError, IndexError, TypeError, ValueError):
                            LOGGER.warning("Ignoring malformed 2GIS item response: %s", response.url)
                            return
                        if item_id in collected_ids or len(collected_ids) >= max_records:
                            return
                        try:
                            on_document(document)
                            collected_ids.add(item_id)
                        except Exception as error:
                            callback_errors.append(error)

                    page.on("response", handle_response)
                    page.goto(url, wait_until="domcontentloaded", referer="https://www.google.com/")
                    page.wait_for_selector("a[href*='?stat=']")
                    self._raise_callback_error(callback_errors)

                    visited_hrefs: set[str] = set()
                    while len(collected_ids) < max_records:
                        item_links = page.locator("a[href*='?stat=']")
                        link_count = item_links.count()
                        if link_count == 0 and not collected_ids:
                            raise BrowserContractError("No 2GIS result links found")

                        clicked_on_page = 0
                        for index in range(link_count):
                            if len(collected_ids) >= max_records:
                                break
                            link = item_links.nth(index)
                            href = link.get_attribute("href")
                            if not href or href in visited_hrefs or not _ITEM_LINK_PATTERN.search(href):
                                continue
                            visited_hrefs.add(href)
                            clicked_on_page += 1
                            before = len(collected_ids)
                            link.click(timeout=self._timeout_ms, force=True)
                            self._wait_for_new_document(page, collected_ids, before, min(self._timeout_ms, 10_000))
                            self._raise_callback_error(callback_errors)
                            if len(collected_ids) == before:
                                LOGGER.warning("2GIS item click produced no catalog payload: %s", href)

                        if len(collected_ids) >= max_records:
                            break
                        next_link = page.locator("a[href*='/page/']").filter(
                            has_text=re.compile(r"^\s*(Следующая|Next|›|>)\s*$")
                        )
                        if next_link.count() == 0:
                            break
                        next_href = next_link.first.get_attribute("href")
                        if not next_href or next_href in visited_hrefs:
                            break
                        visited_hrefs.add(next_href)
                        next_link.first.click(force=True)
                        page.wait_for_timeout(1000)
                        self._raise_callback_error(callback_errors)
                        if clicked_on_page == 0:
                            break
                except Exception as error:
                    artifact_paths = self._write_failure_artifacts(
                        context,
                        page,
                        trace_path,
                        screenshot_path,
                        url=url,
                        error=error,
                    )
                    detail = f"{error}; debug artifacts: {', '.join(str(path) for path in artifact_paths)}"
                    if self._page_looks_like_captcha(page):
                        raise BrowserCaptchaError(detail) from error
                    if isinstance(error, BrowserError):
                        raise type(error)(detail) from error
                    if isinstance(error, PlaywrightTimeoutError):
                        raise BrowserTimeoutError(detail) from error
                    raise BrowserError(detail) from error
                else:
                    context.tracing.stop()
                finally:
                    context.close()
                    browser.close()
        except PlaywrightTimeoutError as error:
            raise BrowserTimeoutError(str(error)) from error
        except BrowserError:
            raise
        except Exception as error:
            if callback_errors and error is callback_errors[0]:
                raise
            raise BrowserError(str(error)) from error

        return len(collected_ids)

    @staticmethod
    def _raise_callback_error(errors: list[Exception]) -> None:
        if errors:
            raise errors[0]

    @staticmethod
    def _wait_for_new_document(page: Any, collected_ids: set[str], previous_count: int, timeout_ms: int) -> None:
        elapsed = 0
        while len(collected_ids) == previous_count and elapsed < timeout_ms:
            page.wait_for_timeout(250)
            elapsed += 250

    @staticmethod
    def _page_looks_like_captcha(page: Any) -> bool:
        try:
            text = f"{page.title()}\n{page.locator('body').inner_text(timeout=2_000)}"
        except Exception:
            return False
        return bool(_CAPTCHA_PATTERN.search(text))

    @staticmethod
    def _write_failure_artifacts(
        context: Any,
        page: Any,
        trace_path: Path,
        screenshot_path: Path,
        *,
        url: str,
        error: Exception,
    ) -> list[Path]:
        written: list[Path] = []
        if context is not None:
            try:
                context.tracing.stop(path=trace_path)
                written.append(trace_path)
            except Exception:
                LOGGER.exception("Unable to save browser failure trace")
        if page is None:
            return written
        try:
            page.screenshot(path=screenshot_path, full_page=True)
            written.append(screenshot_path)
        except Exception:
            LOGGER.exception("Unable to save browser failure screenshot")
        html_path = screenshot_path.with_suffix(".html")
        try:
            html_path.write_text(page.content(), encoding="utf-8")
            written.append(html_path)
        except Exception:
            LOGGER.exception("Unable to save browser failure HTML")
        metadata_path = screenshot_path.with_suffix(".json")
        try:
            metadata_path.write_text(
                json.dumps(
                    {
                        "captured_at": datetime.now(UTC).isoformat(),
                        "requested_url": url,
                        "page_url": page.url,
                        "title": page.title(),
                        "error_type": type(error).__name__,
                        "error": str(error),
                        "captcha_suspected": PlaywrightBrowserAdapter._page_looks_like_captcha(page),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            written.append(metadata_path)
        except Exception:
            LOGGER.exception("Unable to save browser failure metadata")
        return written
