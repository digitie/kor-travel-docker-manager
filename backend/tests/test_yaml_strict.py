"""GM-11: `load_yaml_rejecting_duplicate_keys` 자체의 회귀 테스트.

`compose_service.py`에 있던 기존 구현을 그대로 옮긴 것이지만, 옮기기 전까지
이 로직 자체를 직접 겨냥한 전용 테스트가 없었다.
"""

from __future__ import annotations

import importlib
import io
import subprocess
import sys
from pathlib import Path

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


strict = importlib.import_module("kor_travel_docker_manager.services.yaml_strict")


class _PythonUniqueKeyLoader(yaml.SafeLoader):
    """기존 Python 안전 파서에 같은 중복 키 생성자를 적용한 비교 기준."""


_PythonUniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    strict._construct_unique_yaml_mapping,
)


def test_strict_loader_prefers_available_safe_c_parser() -> None:
    if not hasattr(yaml, "CSafeLoader"):
        pytest.skip("설치된 PyYAML에 libyaml이 없음")
    assert issubclass(strict._UniqueKeyFastSafeLoader, yaml.CSafeLoader)
    assert strict.UniqueKeySafeLoader.__bases__ == (yaml.SafeLoader,)


def test_installed_compose_model_matches_previous_python_strict_parser() -> None:
    source = (Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    assert strict.load_yaml_rejecting_duplicate_keys(source) == yaml.load(
        source, Loader=_PythonUniqueKeyLoader,
    )


def test_strict_loader_keeps_alias_merge_and_scalar_meaning() -> None:
    source = """
base: &defaults
  caption: 한글 지도
  active: true
  missing: null
  count: 012
  temperature: -1.5
  connection: ${STATUS_HOST:-localhost}
alias: *defaults
service:
  <<: *defaults
  label: 운행 중
"""
    result = strict.load_yaml_rejecting_duplicate_keys(source)
    assert result == yaml.load(source, Loader=_PythonUniqueKeyLoader)
    assert result["alias"] is result["base"]
    assert result["service"]["caption"] == "한글 지도"
    assert result["base"]["active"] is True
    assert result["base"]["missing"] is None
    assert result["base"]["count"] == 10
    assert result["base"]["temperature"] == -1.5
    assert result["base"]["connection"] == "${STATUS_HOST:-localhost}"


@pytest.mark.parametrize("source", [
    "a: 1\na: 2\n",
    "root: {nested: 1, nested: 2}",
    "base: &base {a: 1}\nmerged: {<<: *base, a: 2}",
    "a: &a {value: 1}\nb: &b {value: 2}\nmerged: {<<: [*a, *b]}",
    "true: yes\n1: no\n",
    "false: yes\n0: no\n",
    "1: yes\n01: no\n",
])
def test_strict_c_and_python_both_reject_duplicate_and_equivalent_keys(source: str) -> None:
    for parse in (
        strict.load_yaml_rejecting_duplicate_keys,
        lambda text: yaml.load(text, Loader=_PythonUniqueKeyLoader),
    ):
        with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key"):
            parse(source)


def test_strict_loader_keeps_unhashable_mapping_key_rejection() -> None:
    for parse in (
        strict.load_yaml_rejecting_duplicate_keys,
        lambda text: yaml.load(text, Loader=_PythonUniqueKeyLoader),
    ):
        with pytest.raises(yaml.constructor.ConstructorError, match="unhashable mapping key"):
            parse("? [a, b]\n: invalid\n")


@pytest.mark.parametrize("source", [
    "!!python/object:builtins.object {}",
    "!!python/object/apply:builtins.eval ['1 + 1']",
    "!!python/name:builtins.eval",
])
def test_strict_loader_refuses_python_object_tags(source: str) -> None:
    with pytest.raises(yaml.constructor.ConstructorError):
        strict.load_yaml_rejecting_duplicate_keys(source)


@pytest.mark.parametrize("source", ["a: [", "a: {x: 1", "a: \tbad"])
def test_strict_loader_rejects_malformed_yaml(source: str) -> None:
    with pytest.raises(yaml.YAMLError):
        strict.load_yaml_rejecting_duplicate_keys(source)


@pytest.mark.parametrize("source", ["a: 1", "a: 1\na: 2", "a: ["])
def test_strict_loader_disposes_after_success_and_parse_failure(
    monkeypatch: pytest.MonkeyPatch, source: str,
) -> None:
    disposed = []

    class TrackingLoader(strict.UniqueKeySafeLoader):
        def dispose(self):
            disposed.append(True)
            super().dispose()

    class TrackingFastLoader(strict._UniqueKeyFastSafeLoader):
        def dispose(self):
            disposed.append(True)
            super().dispose()

    monkeypatch.setattr(strict, "UniqueKeySafeLoader", TrackingLoader)
    monkeypatch.setattr(strict, "_UniqueKeyFastSafeLoader", TrackingFastLoader)
    try:
        strict.load_yaml_rejecting_duplicate_keys(source)
    except yaml.YAMLError:
        pass
    assert disposed == [True]


def test_strict_loader_without_c_extension_uses_isolated_python_fallback() -> None:
    # 자식 프로세스의 yaml만 바꿔 다른 테스트·이미 로드된 제품 모듈에 영향을 주지 않는다.
    script = """
import runpy, sys, yaml
if hasattr(yaml, 'CSafeLoader'):
    del yaml.CSafeLoader
namespace = runpy.run_path(sys.argv[1])
assert namespace['UniqueKeySafeLoader'].__bases__ == (yaml.SafeLoader,)
assert namespace['_UniqueKeyFastSafeLoader'].__bases__ == (yaml.SafeLoader,)
parse = namespace['load_yaml_rejecting_duplicate_keys']
assert parse('한글: true') == {'한글': True}
try:
    parse('a: 1\\na: 2')
except yaml.constructor.ConstructorError:
    pass
else:
    raise AssertionError('duplicate accepted')
try:
    parse('!!python/object:builtins.object {}')
except yaml.constructor.ConstructorError:
    pass
else:
    raise AssertionError('unsafe object accepted')
print('safe fallback passed')
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, strict.__file__],
        capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "safe fallback passed"


@pytest.mark.parametrize("source", [
    'a: "\t"',
    "a: |\n  literal\tTAB\n",
    '"key\tpart": value',
])
def test_literal_tabs_preserve_accepted_python_yaml(source: str) -> None:
    assert strict.load_yaml_rejecting_duplicate_keys(source) == yaml.load(
        source, Loader=_PythonUniqueKeyLoader,
    )


@pytest.mark.parametrize("source", [b"a: 1", io.StringIO("a: 1")])
def test_non_string_sources_keep_previous_python_loader(source) -> None:
    assert strict.load_yaml_rejecting_duplicate_keys(source) == {"a": 1}


def test_tab_source_chooses_python_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    class UnexpectedFastLoader:
        def __init__(self, source):
            raise AssertionError("literal TAB was given to C loader")

    monkeypatch.setattr(strict, "_UniqueKeyFastSafeLoader", UnexpectedFastLoader)
    with pytest.raises(yaml.YAMLError):
        strict.load_yaml_rejecting_duplicate_keys("a: \tbad")
    assert strict.load_yaml_rejecting_duplicate_keys('a: "\t"') == {"a": "\t"}


def test_c_parse_failure_is_not_retried_with_python(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class RejectingFastLoader:
        def __init__(self, source):
            calls.append("fast-init")

        def get_single_data(self):
            raise yaml.YAMLError("rejected by selected parser")

        def dispose(self):
            calls.append("fast-dispose")

    class UnexpectedPythonLoader:
        def __init__(self, source):
            raise AssertionError("parse error retried through Python")

    monkeypatch.setattr(strict, "_UniqueKeyFastSafeLoader", RejectingFastLoader)
    monkeypatch.setattr(strict, "UniqueKeySafeLoader", UnexpectedPythonLoader)
    with pytest.raises(yaml.YAMLError, match="rejected by selected parser"):
        strict.load_yaml_rejecting_duplicate_keys("a: 1")
    assert calls == ["fast-init", "fast-dispose"]
