from stage1_2gis.google_sheets import normalize_google_domain


def test_normalize_google_domain_matches_stage3_domain_key():
    assert normalize_google_domain("HTTPS://WWW.Example.RU/path?q=1") == "example.ru"
    assert normalize_google_domain("www.пример.рф") == "xn--e1afmkfd.xn--p1ai"
    assert normalize_google_domain("-") is None
