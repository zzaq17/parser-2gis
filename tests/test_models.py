import json

import pytest
from pydantic import ValidationError

from parser_2gis.chrome.dom import DOMNode
from parser_2gis.config import Configuration
from parser_2gis.writer.models import CatalogItem
from parser_2gis.writer.models.adm_div_item import AdmDivItem
from parser_2gis.writer.models.schedule import Schedule


def test_nested_configuration_validates_assignment():
    config = Configuration()

    with pytest.raises(ValidationError):
        config.parser.max_records = 0

    with pytest.raises(ValidationError):
        config.writer.csv.columns_per_entity = 0


def test_configuration_round_trip(tmp_path):
    config_path = tmp_path / 'parser-2gis.config'
    config = Configuration(path=config_path)
    config.parser.max_records = 25
    config.writer.encoding = 'utf-8'

    config.save_config()
    saved = json.loads(config_path.read_text(encoding='utf-8'))
    loaded = Configuration.load_config(config_path)

    assert 'path' not in saved
    assert loaded.path == config_path
    assert loaded.parser.max_records == 25
    assert loaded.writer.encoding == 'utf-8'


def test_models_do_not_share_mutable_defaults():
    first_node = DOMNode(
        nodeId=1,
        backendNodeId=1,
        nodeType=1,
        nodeName='div',
        localName='div',
        nodeValue='',
    )
    second_node = DOMNode(
        nodeId=2,
        backendNodeId=2,
        nodeType=1,
        nodeName='span',
        localName='span',
        nodeValue='',
    )
    first_node.children.append(second_node)

    first_item = CatalogItem(id='1', locale='ru_RU', type='branch')
    second_item = CatalogItem(id='2', locale='ru_RU', type='branch')
    first_item.adm_div.append(AdmDivItem(name='Москва', type='city'))

    assert second_node.children == []
    assert second_item.adm_div == []


def test_schedule_string_uses_weekday_fields():
    schedule = Schedule.model_validate({
        'Mon': {'working_hours': [{'from': '09:00', 'to': '18:00'}]},
        'comment': 'по записи',
    })

    assert schedule.to_str('; ', add_comment=True) == 'Пн: 09:00-18:00 (по записи)'
