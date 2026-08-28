"""사전 점검(KUM-M7) 계약 테스트.

이 모듈의 요점은 "모른다고 말할 줄 아는가"다. 근거 없이 초록불을 켜면 사람이
pinset 하나와 반나절을 태우고, terminal 규약 때문에 그 pinset은 되돌릴 수도 없다.
그래서 각 검사의 unknown 경로를 ok/missing 경로만큼 촘촘히 고정한다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import deployment_readiness as readiness
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

MAP_REVISION = "a" * 40
PINVI_REVISION = "b" * 40


@pytest.fixture(autouse=True)
def _clear_cache():
    readiness.clear_deployment_readiness_cache()
    yield
    readiness.clear_deployment_readiness_cache()


def _completed(returncode: int) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=["x"], returncode=returncode)


def _sibling_tree(tmp_path: Path) -> dict[str, str]:
    """docker-compose.yml이 bind mount하는 사이드카 파일들을 만든다."""

    pinvi = tmp_path / "pinvi" / "infra" / "postgres"
    pinvi.mkdir(parents=True)
    (pinvi / "bootstrap-pinvi-runtime-role.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    map_docker = tmp_path / "kor-travel-map" / "docker"
    map_docker.mkdir(parents=True)
    (map_docker / "postgres-role-bootstrap.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    map_scripts = tmp_path / "kor-travel-map" / "scripts"
    map_scripts.mkdir(parents=True)
    (map_scripts / "database-credential-preflight.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    for path in (
        pinvi / "bootstrap-pinvi-runtime-role.sh",
        map_docker / "postgres-role-bootstrap.sh",
        map_scripts / "database-credential-preflight.sh",
    ):
        os.chmod(path, 0o755)
    return {
        "PINVI_REPO_DIR": str(tmp_path / "pinvi"),
        "KOR_TRAVEL_MAP_REPO_DIR": str(tmp_path / "kor-travel-map"),
    }


# --- 공개 진입점: 절대 던지지 않는다 -----------------------------------------


def test_public_entry_point_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """진단 패널이 500을 내면 운영자는 상태를 볼 유일한 창을 잃는다."""

    def explode() -> dict[str, Any]:
        raise RuntimeError("호스트를 읽을 수 없음")

    monkeypatch.setattr(readiness, "_probe_deployment_readiness", explode)

    payload = readiness.read_deployment_readiness()

    assert payload["schema"] == readiness.DEPLOYMENT_READINESS_SCHEMA
    assert payload["summary"]["state"] == "unverified"
    assert [check["id"] for check in payload["checks"]] == list(readiness._CHECK_ORDER)
    assert all(check["state"] == "unknown" for check in payload["checks"])


def test_unknown_payload_has_the_same_shape_as_a_real_payload() -> None:
    """UI가 코드 경로를 하나만 갖게 하려면 실패 payload도 모양이 같아야 한다."""

    unknown = readiness._unknown_payload("근거 없음")

    assert set(unknown) == {
        "schema",
        "generated_at",
        "cached",
        "cache_age_seconds",
        "summary",
        "checks",
        "unavailable_checks",
    }
    for check in unknown["checks"]:
        assert set(check) == {"id", "state", "label_ko", "detail", "source", "evidence"}


def test_wheelhouse_is_declared_unavailable_rather_than_guessed() -> None:
    """검사할 수 없는 항목은 초록불로 만들지 않고 '검사하지 않는다'고 말한다."""

    payload = readiness._unknown_payload("x")

    ids = {entry["id"] for entry in payload["unavailable_checks"]}
    assert "offline_wheelhouse" in ids
    assert "offline_wheelhouse" not in {check["id"] for check in payload["checks"]}


def test_result_is_cached_within_the_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def probe() -> dict[str, Any]:
        calls["count"] += 1
        return readiness._unknown_payload("probe")

    monkeypatch.setattr(readiness, "_probe_deployment_readiness", probe)

    first = readiness.read_deployment_readiness()
    second = readiness.read_deployment_readiness()

    assert calls["count"] == 1
    assert first["cached"] is False
    assert second["cached"] is True


# --- 상태 우선순위 -----------------------------------------------------------


def test_a_known_blocker_outranks_an_unknown() -> None:
    """unknown이 missing을 덮으면 사람이 실제로 막고 있는 것부터 고치지 못한다."""

    assert readiness._worst_state(["ok", "unknown", "missing"]) == "missing"
    assert readiness._worst_state(["ok", "warn", "unknown"]) == "unknown"
    assert readiness._worst_state(["ok", "warn"]) == "warn"
    assert readiness._worst_state([]) == "ok"


# --- 검사 1: single-file Compose ---------------------------------------------


def test_compose_check_is_unknown_without_environment() -> None:
    check = readiness._check_compose_single_file(None)

    assert check.state == "unknown"
    assert check.source == "none"


def test_compose_check_is_ok_without_override_or_ambient_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness, "get_override_path", lambda: str(tmp_path / "absent.yml"))

    check = readiness._check_compose_single_file({})

    assert check.state == "ok"
    assert check.evidence["override_present"] is False


def test_compose_check_blocks_on_a_present_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """관측된 실제 blocker — legacy override가 승인된 재구축 전체를 막았다."""

    override = tmp_path / "docker-compose.override.yml"
    override.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(readiness, "get_override_path", lambda: str(override))

    check = readiness._check_compose_single_file({})

    assert check.state == "missing"
    assert check.evidence["override_present"] is True


def test_compose_check_blocks_on_compose_file_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness, "get_override_path", lambda: str(tmp_path / "absent.yml"))

    check = readiness._check_compose_single_file({"COMPOSE_FILE": "a.yml:b.yml"})

    assert check.state == "missing"
    assert check.evidence["ambient_blocking"] == ["COMPOSE_FILE"]


def test_compose_check_only_warns_on_advisory_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """막지 않는 것을 막힌다고 말하면 사람이 엉뚱한 것을 고친다."""

    monkeypatch.setattr(readiness, "get_override_path", lambda: str(tmp_path / "absent.yml"))

    check = readiness._check_compose_single_file({"COMPOSE_PROFILES": "dev"})

    assert check.state == "warn"
    assert check.evidence["ambient_advisory"] == ["COMPOSE_PROFILES"]


# --- 검사 2: 사이드카 스크립트 ------------------------------------------------


def test_sibling_check_is_ok_when_every_script_is_a_regular_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness, "_compose_directory", lambda: tmp_path)
    values = _sibling_tree(tmp_path)

    check = readiness._check_sibling_bootstrap_scripts(values)

    assert check.state == "ok"
    assert check.evidence["present"] == 3


def test_sibling_check_reports_a_missing_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness, "_compose_directory", lambda: tmp_path)
    values = _sibling_tree(tmp_path)
    (tmp_path / "pinvi" / "infra" / "postgres" / "bootstrap-pinvi-runtime-role.sh").unlink()

    check = readiness._check_sibling_bootstrap_scripts(values)

    assert check.state == "missing"
    assert check.evidence["present"] == 2


def test_sibling_check_treats_an_auto_created_directory_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """short bind syntax는 source 부재 시 Docker가 **빈 디렉터리**를 만든다.

    존재 여부만 보는 점검은 그 순간 초록이 되고, 실패는 컨테이너 안에서만 난다.
    """

    monkeypatch.setattr(readiness, "_compose_directory", lambda: tmp_path)
    values = _sibling_tree(tmp_path)
    script = tmp_path / "pinvi" / "infra" / "postgres" / "bootstrap-pinvi-runtime-role.sh"
    script.unlink()
    script.mkdir()

    check = readiness._check_sibling_bootstrap_scripts(values)

    assert check.state == "missing"
    assert "디렉터리" in check.detail


def test_sibling_check_warns_on_a_world_writable_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX 퍼미션 계약")
    monkeypatch.setattr(readiness, "_compose_directory", lambda: tmp_path)
    values = _sibling_tree(tmp_path)
    os.chmod(tmp_path / "pinvi" / "infra" / "postgres" / "bootstrap-pinvi-runtime-role.sh", 0o777)

    check = readiness._check_sibling_bootstrap_scripts(values)

    assert check.state == "warn"


def test_sibling_check_is_unknown_when_the_repository_path_is_unresolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness, "_compose_directory", lambda: tmp_path)

    check = readiness._check_sibling_bootstrap_scripts(
        {"PINVI_REPO_DIR": str(tmp_path / "nope"), "KOR_TRAVEL_MAP_REPO_DIR": str(tmp_path / "no")}
    )

    assert check.state == "unknown"


# --- 검사 3: Map base image --------------------------------------------------


def _pins(status: str = "ok", revision: str = MAP_REVISION) -> dict[str, Any]:
    return {
        "status": status,
        "sources": [
            {"role": "map", "url": "u", "revision": revision},
            {"role": "pinvi", "url": "u", "revision": PINVI_REVISION},
        ],
    }


def test_base_image_check_refuses_an_untrusted_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    """비교 기준을 신뢰할 수 없으면 답도 신뢰할 수 없다."""

    monkeypatch.setattr(readiness, "read_published_runtime_pins", lambda: _pins("degraded"))

    check = readiness._check_map_python_base_images({})

    assert check.state == "unknown"
    assert "degraded" in check.detail


def test_base_image_check_refuses_when_the_checkout_is_not_the_pinned_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """살아 있는 체크아웃은 고정된 트리가 아니다 — 정직성 요건."""

    monkeypatch.setattr(readiness, "_compose_directory", lambda: tmp_path)
    monkeypatch.setattr(readiness, "read_published_runtime_pins", lambda: _pins())
    monkeypatch.setattr(readiness, "_git_text", lambda root, args: "c" * 40)
    docker_calls: list[str] = []
    monkeypatch.setattr(
        readiness, "_docker_daemon_reachable", lambda: docker_calls.append("called") or True
    )
    values = _sibling_tree(tmp_path)

    check = readiness._check_map_python_base_images(values)

    assert check.state == "unknown"
    assert check.evidence["head_revision"] == "c" * 40
    # HEAD가 다르면 docker를 부를 이유가 없다 — 부르면 다른 트리를 관측하게 된다.
    assert docker_calls == []


def test_base_image_check_refuses_a_dirty_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness, "_compose_directory", lambda: tmp_path)
    monkeypatch.setattr(readiness, "read_published_runtime_pins", lambda: _pins())

    def git_text(root: Path, args: list[str]) -> str:
        return MAP_REVISION if args[0] == "rev-parse" else " M docker/api.Dockerfile"

    monkeypatch.setattr(readiness, "_git_text", git_text)
    values = _sibling_tree(tmp_path)

    check = readiness._check_map_python_base_images(values)

    assert check.state == "unknown"
    assert "clean" in check.detail


def _clean_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    monkeypatch.setattr(readiness, "_compose_directory", lambda: tmp_path)
    monkeypatch.setattr(readiness, "read_published_runtime_pins", lambda: _pins())
    monkeypatch.setattr(
        readiness,
        "_git_text",
        lambda root, args: MAP_REVISION if args[0] == "rev-parse" else "",
    )
    return _sibling_tree(tmp_path)


def test_base_image_check_is_unknown_when_the_docker_daemon_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """image 부재와 daemon 불가를 구분하지 못하면 멀쩡한 호스트에 거짓 차단이 뜬다."""

    values = _clean_checkout(monkeypatch, tmp_path)
    monkeypatch.setattr(
        readiness,
        "map_application_300_python_base_references_from_root",
        lambda root: ("python@sha256:" + "d" * 64,),
    )
    monkeypatch.setattr(readiness, "_docker_daemon_reachable", lambda: False)
    inspected: list[str] = []
    monkeypatch.setattr(
        readiness, "_local_image_present", lambda ref: inspected.append(ref) or True
    )

    check = readiness._check_map_python_base_images(values)

    assert check.state == "unknown"
    assert check.source == "docker_cli"
    assert inspected == []


def test_base_image_check_blocks_when_a_pinned_base_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """관측된 실제 blocker — exact base가 없어 candidate build가 fail-close했다."""

    values = _clean_checkout(monkeypatch, tmp_path)
    reference = "python@sha256:" + "d" * 64
    monkeypatch.setattr(
        readiness,
        "map_application_300_python_base_references_from_root",
        lambda root: (reference,),
    )
    monkeypatch.setattr(readiness, "_docker_daemon_reachable", lambda: True)
    monkeypatch.setattr(readiness, "_local_image_present", lambda ref: False)

    check = readiness._check_map_python_base_images(values)

    assert check.state == "missing"
    assert reference in check.detail
    assert check.evidence["present"] == 0


def test_base_image_check_is_ok_when_every_base_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _clean_checkout(monkeypatch, tmp_path)
    monkeypatch.setattr(
        readiness,
        "map_application_300_python_base_references_from_root",
        lambda root: ("python@sha256:" + "d" * 64,),
    )
    monkeypatch.setattr(readiness, "_docker_daemon_reachable", lambda: True)
    monkeypatch.setattr(readiness, "_local_image_present", lambda ref: True)

    check = readiness._check_map_python_base_images(values)

    assert check.state == "ok"
    assert check.evidence["present"] == 1


def test_base_image_check_is_unknown_on_a_malformed_dockerfile_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _clean_checkout(monkeypatch, tmp_path)

    def raise_contract(root: Path) -> tuple[str, ...]:
        raise DeploymentContractError("Map application candidate base image contract is invalid")

    monkeypatch.setattr(
        readiness, "map_application_300_python_base_references_from_root", raise_contract
    )

    check = readiness._check_map_python_base_images(values)

    assert check.state == "unknown"
    assert "contract is invalid" in check.detail


# --- 프로세스 실행 규약 --------------------------------------------------------


def test_read_only_runner_returns_none_instead_of_raising() -> None:
    """탐침 실패는 예외가 아니라 None이다 — 진단이 진단을 막으면 안 된다."""

    assert readiness._run_read_only(["definitely-not-a-real-binary-xyz"], timeout=1.0) is None


def test_read_only_runner_passes_a_minimal_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return _completed(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    readiness._run_read_only(["docker", "version"], timeout=3.0)

    assert captured["timeout"] == 3.0
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    assert set(captured["env"]) <= {"PATH", "DOCKER_CONFIG", "DOCKER_HOST", "XDG_RUNTIME_DIR"}


def test_daemon_probe_distinguishes_failure_from_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readiness, "_run_read_only", lambda *a, **k: None)
    assert readiness._docker_daemon_reachable() is None

    monkeypatch.setattr(readiness, "_run_read_only", lambda *a, **k: _completed(1))
    assert readiness._docker_daemon_reachable() is False

    monkeypatch.setattr(readiness, "_run_read_only", lambda *a, **k: _completed(0))
    assert readiness._docker_daemon_reachable() is True


# --- 고정 PinVi revision의 역할 부트스트랩 계약 (KUM-M6) ----------------------

_ALL_MODES_SCRIPT = "\n".join(
    ["#!/bin/sh"] + [f'echo "${{{name}}}"' for name, _ in readiness._PINVI_ROLE_BOOTSTRAP_REQUIRED_MODES]
)


def test_pinvi_mode_check_refuses_an_untrusted_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "read_published_runtime_pins", lambda: _pins("stale"))

    check = readiness._check_pinvi_role_bootstrap_modes({})

    assert check.state == "unknown"
    assert "stale" in check.detail


def test_pinvi_mode_check_reads_the_pinned_blob_not_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """체크아웃이 어느 브랜치에 있든 답이 달라지면 안 된다."""

    monkeypatch.setattr(readiness, "_compose_directory", lambda: tmp_path)
    monkeypatch.setattr(readiness, "read_published_runtime_pins", lambda: _pins())
    seen: list[list[str]] = []

    def git_text(root: Path, args: list[str]) -> str:
        seen.append(list(args))
        return _ALL_MODES_SCRIPT

    monkeypatch.setattr(readiness, "_git_text", git_text)
    values = _sibling_tree(tmp_path)

    check = readiness._check_pinvi_role_bootstrap_modes(values)

    assert check.state == "ok"
    # HEAD를 묻지 않는다 — 고정 revision의 blob을 직접 읽는다.
    assert seen == [["show", f"{PINVI_REVISION}:{readiness._PINVI_ROLE_BOOTSTRAP_SCRIPT}"]]


def test_pinvi_mode_check_blocks_when_a_required_mode_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """모드를 모르는 revision은 주입한 설정을 조용히 무시하고 일반 부트스트랩을 돌린다."""

    monkeypatch.setattr(readiness, "_compose_directory", lambda: tmp_path)
    monkeypatch.setattr(readiness, "read_published_runtime_pins", lambda: _pins())
    monkeypatch.setattr(
        readiness,
        "_git_text",
        lambda root, args: '#!/bin/sh\necho "${PINVI_ROLE_TOPOLOGY_VERIFY_ONLY}"\n',
    )
    values = _sibling_tree(tmp_path)

    check = readiness._check_pinvi_role_bootstrap_modes(values)

    assert check.state == "missing"
    assert check.evidence["missing"] == [
        "PINVI_ROLE_CATALOG_RESET_ONLY",
        "PINVI_ROLE_CATALOG_RESET_PERMIT_FILE",
        "PINVI_ROLE_CATALOG_RESET_RESULT_FILE",
    ]


def test_pinvi_mode_check_is_unknown_when_the_revision_is_not_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fetch되지 않은 revision을 결손으로 보고하면 거짓 차단이 된다."""

    monkeypatch.setattr(readiness, "_compose_directory", lambda: tmp_path)
    monkeypatch.setattr(readiness, "read_published_runtime_pins", lambda: _pins())
    monkeypatch.setattr(readiness, "_git_text", lambda root, args: None)
    values = _sibling_tree(tmp_path)

    check = readiness._check_pinvi_role_bootstrap_modes(values)

    assert check.state == "unknown"
    assert PINVI_REVISION[:12] in check.detail


def test_image_presence_probe_never_pulls(monkeypatch: pytest.MonkeyPatch) -> None:
    """pull은 15분짜리 작업이다 — 패널 폴링마다 그것을 유발하면 안 된다."""

    commands: list[list[str]] = []

    def capture(command, **kwargs):  # type: ignore[no-untyped-def]
        commands.append(list(command))
        return _completed(0)

    monkeypatch.setattr(readiness, "_run_read_only", capture)

    readiness._local_image_present("python@sha256:" + "d" * 64)

    assert commands == [["docker", "image", "inspect", "python@sha256:" + "d" * 64]]
    assert all("pull" not in command for command in commands)
