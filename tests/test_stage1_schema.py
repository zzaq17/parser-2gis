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


def test_schema_exposes_stage3_candidate_view_and_advertising_marker():
    sql = files("stage1_2gis.sql").joinpath("stage1_schema.sql").read_text(encoding="utf-8")

    assert "is_advertised boolean NOT NULL DEFAULT false" in sql
    assert "CREATE OR REPLACE VIEW stage1_2gis.stage3_candidates" in sql
    assert "SELECT url, domain, name, city, rubric, is_advertised" in sql
    assert "stage1_2gis.google_domain_snapshot" in sql
    assert "CREATE OR REPLACE VIEW stage1_2gis.ready_candidates" in sql


def test_schema_migrates_legacy_www_domain_keys_without_losing_urls():
    sql = files("stage1_2gis.sql").joinpath("stage1_schema.sql").read_text(encoding="utf-8")

    assert "canonical.normalized_domain = substring(legacy.normalized_domain FROM 5)" in sql
    assert "link.website_url" in sql
    assert "DELETE FROM stage1_2gis.domains AS legacy" in sql
    assert "UPDATE stage1_2gis.domains" in sql


def test_schema_keeps_sheet_task_identity_and_domain_provenance():
    sql = files("stage1_2gis.sql").joinpath("stage1_schema.sql").read_text(encoding="utf-8")

    assert "task_vertical text" in sql
    assert "task_subniche text" in sql
    assert "CREATE OR REPLACE VIEW stage1_2gis.sheet_task_domain_results" in sql
    assert "array_agg(DISTINCT job.query_key" in sql
