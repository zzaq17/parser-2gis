"""Create Stage 1 URL jobs from the repository cities_list.json file."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CITIES_LIST = REPO_ROOT / "cities_list.json"
DEFAULT_2GIS_CITIES = PACKAGE_ROOT / "parser_2gis" / "data" / "cities.json"
DEFAULT_QUERIES = {
    "dental_clinics": "Стоматологические клиники",
    "child_dental_clinics": "Детские стоматологические клиники",
    "ophthalmology_clinics": "Офтальмологические клиники",
    "multidisciplinary_medical_centers": "Многопрофильные медицинские центры",
}


def load_city_names(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    names: list[str] = []
    for group_names in data.values():
        names.extend(group_names)
    return list(dict.fromkeys(names))


def load_2gis_city_codes(path: Path) -> dict[str, dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["name"]: item for item in data}


def build_url(city: dict[str, str], query: str) -> str:
    base_url = f'https://2gis.{city["domain"]}/{city["code"]}'
    return f"{base_url}/search/{quote(query)}/filters/sort=name"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cities-list", type=Path, default=DEFAULT_CITIES_LIST)
    parser.add_argument("--city-catalog", type=Path, default=DEFAULT_2GIS_CITIES)
    parser.add_argument("--run-id", default=str(uuid.uuid4()))
    parser.add_argument("--command-id", default="cities-medical-search")
    parser.add_argument("--max-records", type=int, default=100)
    parser.add_argument("--limit-cities", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    city_names = load_city_names(args.cities_list)
    if args.limit_cities is not None:
        city_names = city_names[: args.limit_cities]
    city_codes = load_2gis_city_codes(args.city_catalog)

    jobs: list[tuple[str, str, str]] = []
    missing: list[str] = []
    for city_name in city_names:
        city = city_codes.get(city_name)
        if city is None:
            missing.append(city_name)
            continue
        for query_key, query in DEFAULT_QUERIES.items():
            city_key = city["code"]
            jobs.append((city_key, query_key, build_url(city, query)))

    print(json.dumps({"run_id": args.run_id, "job_count": len(jobs), "missing_cities": missing}, ensure_ascii=False))
    if args.dry_run:
        for city_key, query_key, url in jobs:
            print(json.dumps({"city_key": city_key, "query_key": query_key, "url": url}, ensure_ascii=False))
        return 0

    from stage1_2gis.config import PostgresSettings, WorkerSettings
    from stage1_2gis.persistence import Stage1Repository, build_connection_factory

    repository = Stage1Repository(build_connection_factory(PostgresSettings.from_env()))
    settings = WorkerSettings.from_env()
    repository.create_run(
        run_id=args.run_id,
        command_id=args.command_id,
        snapshot={"cities_list": str(args.cities_list), "queries": DEFAULT_QUERIES},
    )
    for city_key, query_key, url in jobs:
        repository.create_job(
            job_id=str(uuid.uuid4()),
            run_id=args.run_id,
            city_key=city_key,
            query_key=query_key,
            source_url=url,
            max_records=args.max_records,
            max_attempts=settings.max_attempts,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        sys.stderr.close()
        raise SystemExit(0)
