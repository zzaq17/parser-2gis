from datetime import UTC, datetime

from stage1_2gis.persistence import Stage1Repository


class _MigrationCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []
        self.executed_many: list[tuple[str, list[tuple]]] = []
        self._rows: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT domain_id, normalized_domain" in sql:
            self._rows = [("puny-id", "xn--e1afmkfd.xn--p1ai"), ("unicode-id", "пример.рф")]
        elif "SELECT source_key, normalized_domain" in sql:
            now = datetime(2026, 7, 15, tzinfo=UTC)
            self._rows = [("input_auto", "xn--e1afmkfd.xn--p1ai", 3, now)]
        elif "SELECT normalized_domain, spreadsheet_id" in sql:
            now = datetime(2026, 7, 15, tzinfo=UTC)
            self._rows = [("xn--e1afmkfd.xn--p1ai", "sheet-id", now)]
        return self

    def executemany(self, sql, params):
        self.executed_many.append((sql, list(params)))

    def fetchall(self):
        return self._rows


def test_stored_punycode_migration_merges_links_and_canonicalizes_tracking_tables():
    cursor = _MigrationCursor()

    Stage1Repository._canonicalize_stored_domains(cursor)

    statements = "\n".join(sql for sql, _ in cursor.executed)
    assert "INSERT INTO stage1_2gis.company_domains" in statements
    assert "DELETE FROM stage1_2gis.company_domains WHERE domain_id = %s" in statements
    assert "DELETE FROM stage1_2gis.domains WHERE domain_id = %s" in statements
    assert cursor.executed_many[0][1][0][1] == "пример.рф"
    assert cursor.executed_many[1][1][0][0] == "пример.рф"
