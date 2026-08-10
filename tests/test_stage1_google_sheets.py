from stage1_2gis.config import SheetTaskSettings
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


class _TaskRequest:
    def __init__(self, callback):
        self.callback = callback

    def execute(self):
        return self.callback()


class _TaskValuesApi:
    def __init__(self) -> None:
        self.updated = []
        self.cleared = []

    def get(self, **kwargs):
        return _TaskRequest(lambda: {"values": []})

    def update(self, **kwargs):
        self.updated.append(kwargs)
        return _TaskRequest(lambda: {"updatedRange": kwargs["range"]})

    def clear(self, **kwargs):
        self.cleared.append(kwargs)
        return _TaskRequest(lambda: {})


class _TaskSheetsApi:
    def __init__(self) -> None:
        self.values_api = _TaskValuesApi()
        self.sheet_ids = {"Ключевые фразы 2GIS": 1}
        self.batches = []

    def values(self):
        return self.values_api

    def get(self, **_kwargs):
        return _TaskRequest(lambda: {"sheets": [{"properties": {"title": title, "sheetId": sheet_id}} for title, sheet_id in self.sheet_ids.items()]})

    def batchUpdate(self, **kwargs):
        self.batches.append(kwargs)

        def update():
            for request in kwargs["body"]["requests"]:
                if "addSheet" in request:
                    name = request["addSheet"]["properties"]["title"]
                    self.sheet_ids.setdefault(name, len(self.sheet_ids) + 1)
            return {"replies": []}

        return _TaskRequest(update)


class _TaskService:
    def __init__(self) -> None:
        self.sheets_api = _TaskSheetsApi()

    def spreadsheets(self):
        return self.sheets_api


def test_task_sheet_client_creates_only_managed_tabs_and_uses_exact_ranges():
    service = _TaskService()
    client = GoogleSheetsQueueClient(service)
    settings = SheetTaskSettings(spreadsheet_id="planning-id")

    client.initialize_task_sheets(settings, [("million", "Москва")])
    client.write_task_summary(settings, [["header"] * 15, ["Медицина", "Стоматология"]])
    client.write_task_results(settings, [["header"] * 12])

    add_names = {
        request["addSheet"]["properties"]["title"]
        for request in service.sheets_api.batches[0]["body"]["requests"]
    }
    assert add_names == {"Города 2GIS", "Сводка 2GIS", "Результаты 2GIS"}
    assert {call["range"] for call in service.sheets_api.values_api.cleared} == {"'Сводка 2GIS'!A:O", "'Результаты 2GIS'!A:L"}
    assert all("NEW domains" not in call["range"] for call in service.sheets_api.values_api.updated)
    validation_batch = service.sheets_api.batches[-1]["body"]["requests"]
    assert sum("setDataValidation" in request for request in validation_batch) == 2
    assert all("fields" not in request["setDataValidation"] for request in validation_batch if "setDataValidation" in request)
