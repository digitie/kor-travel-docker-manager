"""pin 회전 **요청** 저장소 계약 테스트 (KUM-M5).

여기서 지키려는 것은 하나다: 이 파일은 제안일 뿐 pin이 아니다. 그래서 테스트는
"요청이 무엇을 결정할 수 없는가"를 주로 확인한다 — 요청은 URL도 digest도 차단
목록도 정하지 못하고, 대기 중인 다른 요청을 조용히 없애지도 못한다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import runtime_pin_request as request_module
from kor_travel_docker_manager.services.runtime_pin_request import (
    RUNTIME_PIN_REQUEST_SCHEMA,
    RuntimePinRequest,
    RuntimePinRequestError,
    clear_runtime_pin_request,
    prospective_pinset_sha256,
    read_runtime_pin_request,
    runtime_pin_request_path,
    utc_timestamp,
    write_runtime_pin_request,
)

REQUEST_ID = "6f9619ff-8b86-4d01-b42d-00cf4fc964ff"
OTHER_REQUEST_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
MAP_A = "a" * 40
PINVI_B = "b" * 40
BASE = "1" * 64
PROSPECTIVE = "2" * 64


@pytest.fixture(autouse=True)
def _isolated_request_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "requests" / "runtime-pin-requests.json"
    monkeypatch.setenv(request_module.RUNTIME_PIN_REQUEST_FILE_ENV, str(target))
    return target


def _request(**overrides) -> RuntimePinRequest:
    values = {
        "request_id": REQUEST_ID,
        "role": "map",
        "revision": MAP_A,
        "reason": "새 후보 커밋 고정",
        "requested_by": "admin",
        "requested_at": utc_timestamp(),
        "base_pinset_sha256": BASE,
        "prospective_pinset_sha256": PROSPECTIVE,
    }
    values.update(overrides)
    return RuntimePinRequest(**values)


# --- 형식 계약 ---------------------------------------------------------------


def test_request_rejects_a_revision_that_is_not_a_commit_sha() -> None:
    with pytest.raises(RuntimePinRequestError, match="40-hex"):
        _request(revision="HEAD")


def test_request_rejects_an_unknown_role() -> None:
    with pytest.raises(RuntimePinRequestError, match="map or pinvi"):
        _request(role="geo")


def test_request_rejects_a_multiline_reason() -> None:
    # 한 줄 계약이 깨지면 CLI가 읽지 못해 요청이 영원히 적용되지 않는다.
    with pytest.raises(RuntimePinRequestError, match="single line"):
        _request(reason="사유\n두 번째 줄")


def test_a_request_that_changes_nothing_is_malformed() -> None:
    with pytest.raises(RuntimePinRequestError, match="would not change"):
        _request(prospective_pinset_sha256=BASE)


def test_unknown_fields_are_rejected_instead_of_ignored() -> None:
    payload = _request().to_payload()
    payload["approved"] = True

    with pytest.raises(RuntimePinRequestError, match="unknown fields"):
        RuntimePinRequest.from_payload(payload)


def test_a_foreign_schema_is_refused() -> None:
    payload = _request().to_payload()
    payload["schema"] = "something.else.v1"

    with pytest.raises(RuntimePinRequestError, match="schema"):
        RuntimePinRequest.from_payload(payload)


# --- 저장과 읽기 -------------------------------------------------------------


def test_absent_request_reads_as_none_rather_than_an_error() -> None:
    assert read_runtime_pin_request() is None


def test_request_round_trips_through_the_file() -> None:
    original = _request()
    written = write_runtime_pin_request(original)

    loaded = read_runtime_pin_request()

    assert loaded == original
    assert json.loads(written.read_text(encoding="utf-8"))["schema"] == (
        RUNTIME_PIN_REQUEST_SCHEMA
    )


def test_the_request_file_is_owner_only() -> None:
    written = write_runtime_pin_request(_request())

    assert written.stat().st_mode & 0o077 == 0


def test_a_second_request_never_silently_replaces_the_pending_one() -> None:
    write_runtime_pin_request(_request())

    with pytest.raises(RuntimePinRequestError, match="already pending"):
        write_runtime_pin_request(_request(request_id=OTHER_REQUEST_ID, role="pinvi"))

    still_there = read_runtime_pin_request()
    assert still_there is not None and still_there.request_id == REQUEST_ID


def test_a_corrupt_request_file_fails_closed() -> None:
    path = runtime_pin_request_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(RuntimePinRequestError, match="valid JSON"):
        read_runtime_pin_request()


@pytest.mark.skipif(os.name == "nt", reason="POSIX 권한 비트가 있는 환경에서만 의미가 있다")
def test_a_world_writable_request_file_is_refused() -> None:
    written = write_runtime_pin_request(_request())
    os.chmod(written, 0o666)

    with pytest.raises(RuntimePinRequestError, match="world writable"):
        read_runtime_pin_request()


# --- 폐기 --------------------------------------------------------------------


def test_clearing_requires_the_exact_request_id() -> None:
    write_runtime_pin_request(_request())

    # 오래된 화면이 그 사이 들어온 다른 요청을 없애면 안 된다.
    assert clear_runtime_pin_request(expect_request_id=OTHER_REQUEST_ID) is False
    assert read_runtime_pin_request() is not None

    assert clear_runtime_pin_request(expect_request_id=REQUEST_ID) is True
    assert read_runtime_pin_request() is None


def test_clearing_an_absent_request_is_false_not_an_error() -> None:
    assert clear_runtime_pin_request(expect_request_id=REQUEST_ID) is False


# --- digest는 언제나 코드가 계산한다 -----------------------------------------


def test_prospective_digest_is_recomputed_from_canonical_sources() -> None:
    from kor_travel_docker_manager.services.pinned_runtime_release import (
        canonical_pinset_sha256,
        source_specs_for,
    )

    computed = prospective_pinset_sha256(
        release_version=5, map_revision=MAP_A, pinvi_revision=PINVI_B
    )

    assert computed == canonical_pinset_sha256(
        version=5, sources=source_specs_for(map_revision=MAP_A, pinvi_revision=PINVI_B)
    )
    # 같은 revision 쌍은 언제나 같은 digest여야 한다 — 요청이 이 값을 정하지 못한다.
    assert computed == prospective_pinset_sha256(
        release_version=5, map_revision=MAP_A, pinvi_revision=PINVI_B
    )


def test_the_request_store_is_not_a_pin_source() -> None:
    """어떤 로드 경로도 요청 파일을 읽지 않는다는 사실을 코드로 고정한다."""

    from kor_travel_docker_manager.services import (
        compose_service,
        pinned_runtime_release,
    )

    for module in (pinned_runtime_release, compose_service):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "runtime_pin_request" not in source, module.__name__


def test_the_http_layer_never_mutates_the_pin_registry() -> None:
    """HTTP 계층은 registry를 **쓰지 않는다** — 이것이 실제로 강제되는 경계다.

    "backend는 비-root라 물리적으로 못 쓴다"는 논거는 배포에 따라 성립하지 않는다.
    n150 운영 배포는 `.env`가 root `0600`이라 backend를 `sudo -n`으로 띄운다
    (`docs/deploy-runbook.local.md` §3-3). 그 호스트에서 uid 경계는 없으므로, 남는
    보호는 (1) HTTP 계층에 쓰기 경로가 없다는 이 규칙과 (2) apply-pending이 요청에서
    role·revision만 취하고 나머지를 재유도한다는 계약이다. 이 테스트가 (1)을 지킨다.
    """

    import ast

    import kor_travel_docker_manager.api as api_package

    mutators = {
        "rotate_runtime_pin",
        "rotate_runtime_pin_pair",
        "block_runtime_pinset",
        "rollback_runtime_pin",
        "write_runtime_pin_registry",
        "publish_runtime_pins",
        "build_registry",
    }
    # 문자열 검색이 아니라 **AST**로 본다. 주석이나 오류 메시지에 함수 이름이 등장하는
    # 것은 위반이 아니고(오히려 운영자에게 필요한 안내다), 실제 import·호출·속성 접근만
    # 위반이다. 문자열 검색으로 두면 정확한 안내 문구를 쓸 수 없게 된다.
    # `api`는 `__init__.py`가 없는 namespace package라 `__file__`이 None이다.
    api_root = Path(next(iter(api_package.__path__)))
    modules = sorted(api_root.rglob("*.py"))
    assert modules, "api 패키지를 찾지 못했다 — 경로가 바뀌면 이 가드는 무력해진다"
    for module_path in modules:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        referenced: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                referenced.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
        offending = sorted(referenced & mutators)
        assert not offending, f"{module_path.name} references {offending}"
