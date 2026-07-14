from stage1_2gis.persistence import Stage1Repository


class FakeCursor:
    def __init__(self, *, exists=True):
        self.exists = exists
        self.rowcount = 0
        self.calls = []
        self._result = None

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT 1 FROM stage1_2gis.runs"):
            self._result = (1,) if self.exists else None
        elif normalized.startswith("UPDATE stage1_2gis.url_jobs"):
            self.rowcount = 3
        elif "count(*) FILTER (WHERE status = 'queued')" in normalized:
            self._result = (5, 7, 0)

    def fetchone(self):
        return self._result


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        raise AssertionError("rollback was not expected")

    def close(self):
        self.closed = True


def test_resume_run_requeues_interrupted_and_error_jobs():
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    repository = Stage1Repository(lambda: connection)

    result = repository.resume_run("run-id")

    assert result == {
        "requeued_jobs": 3,
        "queued_jobs": 5,
        "completed_jobs": 7,
        "failed_jobs": 0,
    }
    job_update, params = cursor.calls[1]
    assert "status IN ('running', 'retry_wait')" in job_update
    assert "status IN ('failed', 'partial')" in job_update
    assert "THEN 0" in job_update
    assert params == ("run-id", True)
    assert connection.committed
    assert connection.closed


def test_resume_run_returns_none_for_unknown_run():
    cursor = FakeCursor(exists=False)
    repository = Stage1Repository(lambda: FakeConnection(cursor))

    assert repository.resume_run("missing") is None
    assert len(cursor.calls) == 1
