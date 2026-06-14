from importlib.resources import files


def test_schema_contains_required_queue_and_result_tables():
    sql = files("stage1_2gis.sql").joinpath("stage1_schema.sql").read_text(encoding="utf-8")

    for table in (
        "runs",
        "url_jobs",
        "raw_items",
        "branches",
        "companies",
        "domains",
        "company_branches",
        "company_domains",
        "job_item_occurrences",
    ):
        assert f"stage1_2gis.{table}" in sql
