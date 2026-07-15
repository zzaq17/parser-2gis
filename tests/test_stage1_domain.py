from stage1_2gis.domain import normalize_catalog_document, normalize_domain


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
                                {"type": "website", "value": "vk", "url": "https://vk.com/test"},
                                {"type": "phone", "value": "+74950000000", "text": "+7 495 000-00-00"},
                                {"type": "email", "value": "info@example.ru"},
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
    assert item.domains == ("example.ru",)
    assert item.website_domains == (("https://www.example.ru/contacts", "example.ru"),)
    assert item.websites == ("https://www.example.ru/contacts", "https://vk.com/test")


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
