from stage1_2gis.google_sheets import GoogleSheetsQueueClient, export_source, normalize_google_domain


def test_normalize_google_domain_matches_stage3_domain_key():
    assert normalize_google_domain("HTTPS://WWW.Example.RU/path?q=1") == "example.ru"
    assert normalize_google_domain("www.пример.рф") == "пример.рф"
    assert normalize_google_domain("xn--e1afmkfd.xn--p1ai") == "пример.рф"
    assert normalize_google_domain("-") is None


def test_export_source_uses_advertising_label_only_for_advertised_candidates():
    assert export_source(0) == "2ГИС"
    assert export_source(False) == "2ГИС"
    assert export_source(1) == "2ГИС реклама"
    assert export_source(True) == "2ГИС реклама"


class _FakeValuesApi:
    def __init__(self) -> None:
        self.append_calls: list[dict] = []

    def append(self, **kwargs):
        self.append_calls.append(kwargs)
        return self

    def execute(self):
        return {"updates": {"updatedRange": "'NEW domains'!A3:H4"}}


class _FakeSheetsApi:
    def __init__(self) -> None:
        self.values_api = _FakeValuesApi()

    def values(self):
        return self.values_api


class _FakeService:
    def __init__(self) -> None:
        self.sheets_api = _FakeSheetsApi()

    def spreadsheets(self):
        return self.sheets_api


def test_append_candidates_writes_the_new_a_to_h_shape(monkeypatch):
    service = _FakeService()
    monkeypatch.setattr("stage1_2gis.google_sheets.date", type("FixedDate", (), {"today": staticmethod(lambda: __import__("datetime").date(2026, 7, 14))}))

    exported = GoogleSheetsQueueClient(service).append_candidates(
        "spreadsheet-id",
        new_domains_sheet="NEW domains",
        rows=[
            {"url": "https://advertised.example", "domain": "advertised.example", "name": "Ad", "city": "Moscow", "rubric": "Test", "is_advertised": 1},
            {"url": "https://organic.example", "domain": "organic.example", "name": "Organic", "city": "Kazan", "rubric": "Test", "is_advertised": 0},
        ],
    )

    assert exported == 2
    assert service.sheets_api.values_api.append_calls == [{
        "spreadsheetId": "spreadsheet-id",
        "range": "'NEW domains'!A3:H",
        "valueInputOption": "USER_ENTERED",
        "insertDataOption": "INSERT_ROWS",
        "body": {"values": [
            ["https://advertised.example", "advertised.example", "Ad", "Moscow", "Test", 1, "2ГИС реклама", "2026-07-14"],
            ["https://organic.example", "organic.example", "Organic", "Kazan", "Test", 0, "2ГИС", "2026-07-14"],
        ]},
    }]


def test_append_candidates_canonicalizes_and_deduplicates_idn_domains(monkeypatch):
    service = _FakeService()
    monkeypatch.setattr("stage1_2gis.google_sheets.date", type("FixedDate", (), {"today": staticmethod(lambda: __import__("datetime").date(2026, 7, 14))}))

    exported = GoogleSheetsQueueClient(service).append_candidates(
        "spreadsheet-id",
        new_domains_sheet="NEW domains",
        rows=[
            {"url": "https://xn--e1afmkfd.xn--p1ai", "domain": "xn--e1afmkfd.xn--p1ai", "name": "IDN", "city": "Москва", "rubric": "Test", "is_advertised": 0},
            {"url": "https://пример.рф", "domain": "пример.рф", "name": "Duplicate", "city": "Москва", "rubric": "Test", "is_advertised": 0},
        ],
    )

    assert exported == 1
    assert service.sheets_api.values_api.append_calls[0]["body"]["values"][0][1] == "пример.рф"


def test_append_candidates_removes_www_from_exported_domain(monkeypatch):
    service = _FakeService()
    monkeypatch.setattr("stage1_2gis.google_sheets.date", type("FixedDate", (), {"today": staticmethod(lambda: __import__("datetime").date(2026, 7, 14))}))

    exported = GoogleSheetsQueueClient(service).append_candidates(
        "spreadsheet-id",
        new_domains_sheet="NEW domains",
        rows=[
            {"url": "https://www.example.ru/contacts", "domain": "WWW.Example.RU", "name": "WWW", "city": "Москва", "rubric": "Test", "is_advertised": 0},
        ],
    )

    assert exported == 1
    assert service.sheets_api.values_api.append_calls[0]["body"]["values"][0][1] == "example.ru"
