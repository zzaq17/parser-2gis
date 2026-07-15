import json
from importlib.resources import files


def test_packaged_city_catalog_contains_moscow():
    catalog_path = files("stage1_2gis").joinpath("data/cities.json")
    cities = json.loads(catalog_path.read_text(encoding="utf-8"))

    assert any(city["name"] == "Москва" and city["code"] == "moscow" for city in cities)
