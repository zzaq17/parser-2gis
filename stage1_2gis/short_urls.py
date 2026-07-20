"""Bounded resolution of explicitly supported short-link services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SHORTENER_HOSTS = frozenset({"clck.ru"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass(slots=True, frozen=True)
class ShortUrlResolution:
    original_url: str
    final_url: str | None
    redirect_count: int
    status: str
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "resolved" and self.final_url is not None


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def is_shortener_url(value: str | None) -> bool:
    host = _url_host(value)
    return host in SHORTENER_HOSTS


def resolve_short_url(
    value: str,
    *,
    max_redirects: int = 5,
    request_once: Callable[[str], tuple[int, str | None]] | None = None,
) -> ShortUrlResolution:
    """Follow at most ``max_redirects`` without downloading response bodies."""

    original_url = str(value or "").strip()
    current_url = _as_http_url(original_url)
    if current_url is None or not is_shortener_url(current_url):
        return ShortUrlResolution(original_url, None, 0, "not_shortener")
    request_once = request_once or _request_once
    redirects = 0
    while True:
        try:
            status_code, location = request_once(current_url)
        except Exception as error:
            return ShortUrlResolution(original_url, None, redirects, "request_failed", str(error))
        if status_code not in REDIRECT_STATUSES:
            status = "resolved" if redirects else "not_redirected"
            return ShortUrlResolution(original_url, current_url if redirects else None, redirects, status)
        if not location:
            return ShortUrlResolution(original_url, None, redirects, "missing_location")
        if redirects >= max_redirects:
            return ShortUrlResolution(original_url, None, redirects, "redirect_limit")
        next_url = _as_http_url(urljoin(current_url, location))
        if next_url is None:
            return ShortUrlResolution(original_url, None, redirects, "unsafe_redirect")
        current_url = next_url
        redirects += 1


def _request_once(url: str) -> tuple[int, str | None]:
    opener = build_opener(_NoRedirectHandler())
    for method in ("HEAD", "GET"):
        request = Request(
            url,
            method=method,
            headers={"User-Agent": "contacts-pipeline-short-url-resolver/1.0", "Range": "bytes=0-0"},
        )
        try:
            with opener.open(request, timeout=8) as response:
                return int(response.status), response.headers.get("Location")
        except HTTPError as error:
            status_code = int(error.code)
            location = error.headers.get("Location")
            error.close()
            if status_code in REDIRECT_STATUSES:
                return status_code, location
            if method == "HEAD" and status_code in {400, 403, 405, 501}:
                continue
            return status_code, None
    return 501, None


def _as_http_url(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").strip(".").lower()
    if parsed.scheme not in {"http", "https"} or not host or _is_unsafe_host(host):
        return None
    return candidate


def _url_host(value: str | None) -> str | None:
    candidate = _as_http_url(str(value or ""))
    if candidate is None:
        return None
    return (urlsplit(candidate).hostname or "").lower()


def _is_unsafe_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") or "." not in host:
        return True
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return not address.is_global
