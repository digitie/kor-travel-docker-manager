"""GM-11: `load_yaml_rejecting_duplicate_keys` 자체의 회귀 테스트.

`compose_service.py`에 있던 기존 구현을 그대로 옮긴 것이지만, 옮기기 전까지
이 로직 자체를 직접 겨냥한 전용 테스트가 없었다.
"""

from __future__ import annotations

import pytest
import yaml

from kor_travel_docker_manager.services.yaml_strict import (
    load_yaml_rejecting_duplicate_keys,
)


def test_valid_yaml_parses_normally() -> None:
    result = load_yaml_rejecting_duplicate_keys("a: 1\nb: 2\n")
    assert result == {"a": 1, "b": 2}


def test_duplicate_top_level_key_is_rejected() -> None:
    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key"):
        load_yaml_rejecting_duplicate_keys("a: 1\na: 2\n")


def test_duplicate_nested_key_is_rejected() -> None:
    source = """
parent:
  child: 1
  child: 2
"""
    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key"):
        load_yaml_rejecting_duplicate_keys(source)


def test_nested_mappings_without_duplicates_parse_normally() -> None:
    source = """
parent:
  child_a: 1
  child_b: 2
other:
  child_a: 3
"""
    result = load_yaml_rejecting_duplicate_keys(source)
    assert result == {"parent": {"child_a": 1, "child_b": 2}, "other": {"child_a": 3}}
