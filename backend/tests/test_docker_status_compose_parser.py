"""상태 조회의 안전한 Compose 파싱과 최신 파일 반영 계약."""

import importlib
import os
from pathlib import Path

import pytest
import yaml

module = importlib.import_module("kor_travel_docker_manager.services.docker_service")

_DOCUMENT = """
x-default: &defaults
  enabled: true
  disabled: false
  count: 12
  octal: 012
  temperature: -1.5
  empty: null
  caption: 한글 지도
  address: ${STATUS_HOST:-localhost}
services:
  example:
    <<: *defaults
    enabled: false
    ports: ["12201:12201"]
    labels:
      copied: *defaults
"""


def test_status_compose_prefers_available_safe_c_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(yaml, "CSafeLoader"):
        pytest.skip("설치된 PyYAML에 libyaml이 없음")
    path = tmp_path / "compose.yml"
    path.write_text(_DOCUMENT, encoding="utf-8")
    original = yaml.load
    loaders = []

    def observe(stream, Loader):
        loaders.append(Loader)
        return original(stream, Loader=Loader)

    monkeypatch.setattr(yaml, "load", observe)
    result = module.get_compose_config(str(path))
    assert result["services"]["example"]["caption"] == "한글 지도"
    assert loaders == [yaml.CSafeLoader]


def test_status_compose_matches_safe_python_yaml_values(tmp_path: Path) -> None:
    path = tmp_path / "compose.yml"
    path.write_text(_DOCUMENT, encoding="utf-8")
    expected = yaml.load(_DOCUMENT, Loader=yaml.SafeLoader)
    result = module.get_compose_config(str(path))
    assert result == expected
    service = result["services"]["example"]
    assert service["enabled"] is False
    assert service["disabled"] is False
    assert service["count"] == 12 and service["octal"] == 10
    assert service["temperature"] == -1.5 and service["empty"] is None
    assert service["address"] == "${STATUS_HOST:-localhost}"
    assert service["labels"]["copied"] == expected["x-default"]


def test_status_compose_uses_safe_python_parser_when_libyaml_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "compose.yml"
    path.write_text(_DOCUMENT, encoding="utf-8")
    expected = yaml.load(_DOCUMENT, Loader=yaml.SafeLoader)
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    assert module.get_compose_config(str(path)) == expected


@pytest.mark.parametrize("with_c_parser", [True, False])
def test_status_compose_refuses_python_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_c_parser: bool,
) -> None:
    if not with_c_parser:
        monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    path = tmp_path / "compose.yml"
    path.write_text("!!python/object:builtins.object {}", encoding="utf-8")
    assert module.get_compose_config(str(path)) == {}


def test_status_compose_reads_replacement_bytes_even_if_mtime_is_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "compose.yml"
    path.write_text("services: {old: {enabled: true}}", encoding="utf-8")
    before = path.stat()
    first = module.get_compose_config(str(path))
    path.write_text("services: {new: {enabled: true}}", encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    assert module.get_compose_config(str(path)) == {"services": {"new": {"enabled": True}}}
    assert first == {"services": {"old": {"enabled": True}}}


def test_status_compose_does_not_retry_a_c_parse_error_with_another_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(yaml, "CSafeLoader"):
        pytest.skip("설치된 PyYAML에 libyaml이 없음")
    path = tmp_path / "compose.yml"
    path.write_text("services: {}", encoding="utf-8")
    calls = []

    def reject(stream, Loader):
        calls.append(Loader)
        raise yaml.YAMLError("invalid document")

    monkeypatch.setattr(yaml, "load", reject)
    assert module.get_compose_config(str(path)) == {}
    assert calls == [yaml.CSafeLoader]


@pytest.mark.parametrize("content", ["services: [", "", "# 빈 문서\n"])
def test_status_compose_preserves_invalid_and_empty_document_result(
    tmp_path: Path, content: str,
) -> None:
    path = tmp_path / "compose.yml"
    path.write_text(content, encoding="utf-8")
    assert module.get_compose_config(str(path)) == {}


def test_status_compose_preserves_missing_file_result(tmp_path: Path) -> None:
    assert module.get_compose_config(str(tmp_path / "missing.yml")) == {}
