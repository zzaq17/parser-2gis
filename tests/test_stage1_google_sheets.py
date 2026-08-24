from stage1_2gis.config import SheetTaskSettings
from stage1_2gis.google_sheets import GoogleSheetsQueueClient, export_source, normalize_google_domain


def task_settings():
    return SheetTaskSettings(
        spreadsheet_id="planning-id",
        phrases_sheet="Ключевые фразы 2GIS",
        cities_sheet="Города 2GIS",
        summary_sheet="Сводка 2GIS",
        results_sheet="Результаты 2GIS",
    )


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
        self.appended = []
        self.batched = []
        self.read_values = {}

    def get(self, **kwargs):
        return _TaskRequest(lambda: {"values": self.read_values.get(kwargs["range"], [])})

    def update(self, **kwargs):
        self.updated.append(kwargs)
        return _TaskRequest(lambda: {"updatedRange": kwargs["range"]})

    def clear(self, **kwargs):
        self.cleared.append(kwargs)
        return _TaskRequest(lambda: {})

    def append(self, **kwargs):
        self.appended.append(kwargs)
        return _TaskRequest(lambda: {"updates": {"updatedRange": kwargs["range"]}})

    def batchUpdate(self, **kwargs):
        self.batched.append(kwargs)
        return _TaskRequest(lambda: {"totalUpdatedCells": sum(len(row["values"][0]) for row in kwargs["body"]["data"])})


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
    settings = task_settings()

    client.initialize_task_sheets(settings, [("million", "Москва")])
    client.append_missing_task_summary(settings, [["header"] * 15, ["Медицина", "Стоматология"]])
    client.sync_task_summary(settings, [["header"] * 15, ["Медицина", "Стоматология"]])
    client.sync_task_results(settings, [["header"] * 12])

    add_names = {
        request["addSheet"]["properties"]["title"]
        for request in service.sheets_api.batches[0]["body"]["requests"]
    }
    assert add_names == {"Города 2GIS", "Сводка 2GIS", "Результаты 2GIS"}
    assert service.sheets_api.values_api.cleared == []
    assert {call["range"] for call in service.sheets_api.values_api.appended} == {"'Сводка 2GIS'!A:O"}
    assert all("NEW domains" not in call["range"] for call in service.sheets_api.values_api.updated)
    validation_batch = service.sheets_api.batches[-1]["body"]["requests"]
    assert sum("setDataValidation" in request for request in validation_batch) == 2
    assert all("fields" not in request["setDataValidation"] for request in validation_batch if "setDataValidation" in request)


def test_task_sheet_client_extends_legacy_summary_headers_without_replacing_history():
    service = _TaskService()
    service.sheets_api.sheet_ids.update({"Города 2GIS": 2, "Сводка 2GIS": 3, "Результаты 2GIS": 4})
    service.sheets_api.values_api.read_values["'Сводка 2GIS'!A1:O2"] = [
        ["Вертикаль", "Подниша"],
        ["Аренда", "Аренда коммерческих помещений"],
    ]
    client = GoogleSheetsQueueClient(service)

    client.initialize_task_sheets(task_settings(), [])

    extension = next(
        call for call in service.sheets_api.values_api.updated
        if call["range"] == "'Сводка 2GIS'!C1:O1"
    )
    assert extension["body"]["values"] == [[
        "Фраз", "К запуску", "Статус", "run_id", "Городов", "URL-заданий", "Выполнено",
        "Ошибок", "Компаний", "Доменов", "Начато", "Завершено", "Ошибка",
    ]]


def test_task_summary_sync_preserves_unmatched_history_and_appends_only_new_rows(monkeypatch):
    client = GoogleSheetsQueueClient(object())
    settings = task_settings()
    headers = [f"header-{number}" for number in range(15)]
    history = ["Аренда", "Аренда коммерческих помещений", *([""] * 13)]
    current = ["Медицина", "Стоматология", "old", *([""] * 12)]
    replacement = ["Медицина", "Стоматология", "new", *([""] * 12)]
    new_row = ["Медицина", "Офтальмология", "new", *([""] * 12)]
    updates = []
    appended = []
    monkeypatch.setattr(client, "_values", lambda *_args: [headers, history, current])
    monkeypatch.setattr(client, "_write_value_ranges", lambda *_args: updates.append(_args))
    monkeypatch.setattr(client, "_append_values", lambda *_args: appended.append(_args))

    client.sync_task_summary(settings, [headers, replacement, new_row])

    assert updates == [("planning-id", "Сводка 2GIS", [("A3:O3", replacement)])]
    assert appended == [("planning-id", "Сводка 2GIS", "A:O", [new_row])]


def test_task_result_sync_does_not_rewrite_immutable_history(monkeypatch):
    client = GoogleSheetsQueueClient(object())
    settings = task_settings()
    headers = [f"header-{number}" for number in range(12)]
    history = ["run-1", "Медицина", "Стоматология", "old.example", *( [""] * 8)]
    new_row = ["run-1", "Медицина", "Стоматология", "new.example", *( [""] * 8)]
    updates = []
    appended = []
    monkeypatch.setattr(client, "_values", lambda *_args: [headers, history])
    monkeypatch.setattr(client, "_write_value_ranges", lambda *_args: updates.append(_args))
    monkeypatch.setattr(client, "_append_values", lambda *_args: appended.append(_args))

    client.sync_task_results(settings, [headers, history, new_row])

    assert updates == [("planning-id", "Результаты 2GIS", [])]
    assert appended == [("planning-id", "Результаты 2GIS", "A:L", [new_row])]


def test_task_sheet_write_retries_a_rate_limit(monkeypatch):
    class RateLimitError(Exception):
        resp = type("Response", (), {"status": 429})()

    class Request:
        def __init__(self):
            self.calls = 0

        def execute(self):
            self.calls += 1
            if self.calls == 1:
                raise RateLimitError()
            return {"ok": True}

    waits = []
    monkeypatch.setattr("stage1_2gis.google_sheets.sleep", waits.append)
    request = Request()

    assert GoogleSheetsQueueClient(object())._execute_write(request) == {"ok": True}
    assert request.calls == 2
    assert waits == [1]
