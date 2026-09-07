from stage1_2gis.domain import normalize_catalog_document, normalize_domain, resolve_catalog_short_urls
from stage1_2gis.short_urls import ShortUrlResolution, resolve_short_url


def test_normalize_domain():
    assert normalize_domain("HTTPS://WWW.Example.RU/path?q=1") == "example.ru"
    assert normalize_domain("www.example.ru") == normalize_domain("example.ru")
    assert normalize_domain("https://пример.рф/contacts") == "пример.рф"
    assert normalize_domain("https://xn--e1afmkfd.xn--p1ai/contacts") == "пример.рф"
    assert normalize_domain("https://vk.com/example") is None
    assert normalize_domain("localhost") is None
    assert normalize_domain("127.0.0.1") is None


def test_normalize_catalog_document_preserves_website_mapping():
    document = {
        "meta": {"code": 200},
        "result": {
            "items": [
                {
                    "id": "123_branch",
                    "locale": "ru_RU",
                    "type": "branch",
                    "name_ex": {"primary": "Тест", "extension": "клиника"},
                    "address_name": "Москва, Тестовая улица, 1",
                    "adm_div": [{"name": "Москва", "type": "city"}],
                    "rubrics": [{"name": "Стоматологии", "kind": "primary"}],
                    "stat": {"is_advertised": True},
                    "org": {"id": "org-1", "name": "Тест", "branch_count": 1},
                    "contact_groups": [
                        {
                            "contacts": [
                                {"type": "website", "value": "example.ru", "url": "https://www.example.ru/contacts"},
                                {"type": "social", "value": "vk", "url": "https://vk.com/test"},
                                {"type": "email", "value": "info@example.ru", "url": "https://second.example/path"},
                                {"type": "phone", "value": "+74950000000", "text": "+7 495 000-00-00"},
                            ]
                        }
                    ],
                }
            ]
        },
    }

    item = normalize_catalog_document(document)

    assert item.two_gis_item_id == "123_branch"
    assert item.two_gis_org_id == "org-1"
    assert item.name == "Тест"
    assert item.city == "Москва"
    assert item.primary_rubric == "Стоматологии"
    assert item.is_advertised is True
    assert item.domains == ("example.ru", "second.example")
    assert item.website_domains == (
        ("https://www.example.ru/contacts", "example.ru"),
        ("https://second.example/path", "second.example"),
    )
    assert item.websites == (
        "https://www.example.ru/contacts",
        "https://vk.com/test",
        "https://second.example/path",
    )


def test_www_is_removed_only_from_domain_key():
    document = {
        "result": {
            "items": [{
                "id": "www_branch",
                "locale": "ru_RU",
                "type": "branch",
                "name": "WWW test",
                "url": "https://2gis.ru/test",
                "contact_groups": [{
                    "contacts": [{
                        "type": "website",
                        "value": "www.example.ru",
                        "url": "https://www.example.ru/catalog?from=2gis",
                    }],
                }],
            }],
        },
    }

    item = normalize_catalog_document(document)

    assert item.website_domains == (("https://www.example.ru/catalog?from=2gis", "example.ru"),)
    assert item.websites == ("https://www.example.ru/catalog?from=2gis",)


def test_short_url_resolution_keeps_original_and_replaces_domain_mapping():
    document = {
        "result": {
            "items": [{
                "id": "short_branch",
                "locale": "ru_RU",
                "type": "branch",
                "name": "Short URL test",
                "url": "https://2gis.ru/test",
                "contact_groups": [{"contacts": [{
                    "type": "website",
                    "value": "clck.ru",
                    "url": "https://clck.ru/abc123?source=2gis",
                }]}],
            }],
        },
    }
    item = normalize_catalog_document(document)

    resolved = resolve_catalog_short_urls(
        item,
        resolver=lambda url: ShortUrlResolution(
            url, "https://real.example.ru/full/path", 2, "resolved"
        ),
    )

    assert resolved.websites == ("https://clck.ru/abc123?source=2gis",)
    assert resolved.domains == ("real.example.ru",)
    assert resolved.website_domains == (("https://real.example.ru/full/path", "real.example.ru"),)
    assert resolved.normalized_payload["_short_url_resolutions"][0]["original_domain"] == "clck.ru"


def test_failed_short_url_is_not_emitted_as_business_domain():
    document = {
        "result": {
            "items": [{
                "id": "failed_short_branch",
                "locale": "ru_RU",
                "type": "branch",
                "name": "Failed short URL test",
                "url": "https://2gis.ru/test",
                "contact_groups": [{"contacts": [{
                    "type": "website", "value": "clck.ru", "url": "https://clck.ru/broken",
                }]}],
            }],
        },
    }

    resolved = resolve_catalog_short_urls(
        normalize_catalog_document(document),
        resolver=lambda url: ShortUrlResolution(url, None, 5, "redirect_limit"),
    )

    assert resolved.domains == ()
    assert resolved.website_domains == ()
    assert resolved.normalized_payload["_short_url_resolutions"][0]["status"] == "redirect_limit"


def test_resolver_preserves_full_url_and_stops_after_five_redirects():
    calls: list[str] = []
    redirects = {
        "https://clck.ru/a?full=1": (302, "/b?full=2"),
        "https://clck.ru/b?full=2": (302, "https://tracker.example/3"),
        "https://tracker.example/3": (302, "https://tracker.example/4"),
        "https://tracker.example/4": (302, "https://tracker.example/5"),
        "https://tracker.example/5": (302, "https://real.example/path"),
        "https://real.example/path": (200, None),
    }

    def request_once(url: str) -> tuple[int, str | None]:
        calls.append(url)
        return redirects[url]

    result = resolve_short_url(
        "https://clck.ru/a?full=1", max_redirects=5, request_once=request_once
    )

    assert result.succeeded
    assert result.final_url == "https://real.example/path"
    assert result.redirect_count == 5
    assert calls[0] == "https://clck.ru/a?full=1"


def test_resolver_rejects_a_sixth_redirect():
    def request_once(url: str) -> tuple[int, str | None]:
        index = int(url.rsplit("/", 1)[-1])
        return 302, f"https://redirect.example/{index + 1}"

    result = resolve_short_url(
        "https://clck.ru/0", max_redirects=5, request_once=request_once
    )

    assert result.status == "redirect_limit"
    assert result.final_url is None
    assert result.redirect_count == 5
