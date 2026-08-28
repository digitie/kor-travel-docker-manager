"""source-status 관측 카드 계약 테스트.

이 카드의 핵심은 "모르는 것을 모른다고 말하는가"다. 권위 없는 값을 권위처럼 보여
주면 운영자가 없는 문제를 쫓거나 있는 문제를 놓친다. 그래서 unknown 경로를 ok/drift
경로만큼 촘촘히 고정한다.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import source_status as status
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

MAP_REVISION = "a" * 40
OTHER_REVISION = "c" * 40
IMAGE_ID = "sha256:" + "e" * 64
PINNED_BASE = "python@sha256:" + "d" * 64


@pytest.fixture(autouse=True)
def _clear_cache():
    status.clear_source_status_cache()
    yield
    status.clear_source_status_cache()


def _completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr="")


# --- installer provenance -----------------------------------------------------


def test_provenance_missing_file_is_a_normal_outcome(tmp_path: Path) -> None:
    """legacy rsync 배포본에는 설치 기록이 없다 — 오류가 아니다."""

    row = status.read_installer_provenance(root=tmp_path)

    assert row["state"] == "unknown"
    assert row["human"]["level"] == "unverified"
    assert "설치 기록이 없습니다" in row["detail"]


def test_provenance_reads_the_installer_files(tmp_path: Path) -> None:
    (tmp_path / ".ktdm-source-revision").write_text(MAP_REVISION + "\n", encoding="utf-8")
    (tmp_path / ".ktdm-release-manifest.json").write_text(
        json.dumps(
            {
                "manager_source_revision": MAP_REVISION,
                "installed_at": "2026-08-28T00:00:00Z",
                "source_owner_uid": 0,
                "source_owner_gid": 0,
                "env_owner_uid": 1000,
                "env_owner_gid": 1000,
                "backend_distribution": "ktdm-1.0",
                "backend_wheel_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )

    row = status.read_installer_provenance(root=tmp_path)

    assert row["state"] == "recorded"
    assert row["revision"] == MAP_REVISION
    assert row["manifest"]["backend_distribution"] == "ktdm-1.0"


def test_provenance_never_echoes_host_account_layout(tmp_path: Path) -> None:
    """uid/gid는 운영자에게 행동 지침을 주지 않으면서 공격자에게는 정보다."""

    (tmp_path / ".ktdm-source-revision").write_text(MAP_REVISION, encoding="utf-8")
    (tmp_path / ".ktdm-release-manifest.json").write_text(
        json.dumps(
            {
                "manager_source_revision": MAP_REVISION,
                "source_owner_uid": 0,
                "env_owner_uid": 1000,
                "env_owner_gid": 1000,
            }
        ),
        encoding="utf-8",
    )

    row = status.read_installer_provenance(root=tmp_path)

    serialized = json.dumps(row, ensure_ascii=False)
    assert "source_owner_uid" not in serialized
    assert "env_owner_uid" not in serialized


def test_provenance_refuses_to_pick_a_winner_when_the_two_files_disagree(
    tmp_path: Path,
) -> None:
    """부분 덮어쓰기를 의심해야 하는 상황에서 한쪽을 고르면 안 된다."""

    (tmp_path / ".ktdm-source-revision").write_text(MAP_REVISION, encoding="utf-8")
    (tmp_path / ".ktdm-release-manifest.json").write_text(
        json.dumps({"manager_source_revision": OTHER_REVISION}), encoding="utf-8"
    )

    row = status.read_installer_provenance(root=tmp_path)

    assert row["state"] == "inconsistent"
    assert row["revision"] == MAP_REVISION
    assert row["manifest_revision"] == OTHER_REVISION
    assert row["human"]["level"] == "action_required"


def test_provenance_rejects_a_malformed_revision(tmp_path: Path) -> None:
    (tmp_path / ".ktdm-source-revision").write_text("not-a-revision", encoding="utf-8")

    row = status.read_installer_provenance(root=tmp_path)

    assert row["state"] == "unknown"


# --- sibling checkout ---------------------------------------------------------


def test_checkout_row_never_leaks_stderr_or_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """git stderr에는 절대 경로가 그대로 들어 있다."""

    def fail(argv, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(
            args=argv, returncode=128, stdout="", stderr="fatal: /home/secret/path not a repository"
        )

    monkeypatch.setattr(status, "_run_read_only", fail)

    row = status.sibling_checkout_row("geo", "X_REPO", "../geo", "Geo")

    serialized = json.dumps(row, ensure_ascii=False)
    assert "secret" not in serialized
    assert "fatal" not in serialized
    assert row["state"] == "unknown"


def test_checkout_row_reports_clean_and_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(argv, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(argv)
        if "rev-parse" in argv:
            return _completed(0, MAP_REVISION + "\n")
        return _completed(0, " M app.py\n")

    monkeypatch.setattr(status, "_run_read_only", run)

    row = status.sibling_checkout_row("geo", "X_REPO", "../geo", "Geo")

    assert row["state"] == "dirty"
    assert row["revision"] == MAP_REVISION
    assert row["human"]["level"] == "action_required"
    # 조회가 사이드카의 인덱스를 갱신하지 못하게 막는다.
    assert any("--no-optional-locks" in argv for argv in calls)
    # untracked 노이즈로 상시 dirty가 되면 배지가 무의미해진다.
    assert any("--untracked-files=no" in argv for argv in calls)


def test_checkout_row_is_unknown_when_status_fails_even_if_head_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """revision을 알아도 그것이 도는 코드인지 모르면 아는 척하지 않는다."""

    def run(argv, **kwargs):  # type: ignore[no-untyped-def]
        return _completed(0, MAP_REVISION) if "rev-parse" in argv else _completed(1)

    monkeypatch.setattr(status, "_run_read_only", run)

    row = status.sibling_checkout_row("geo", "X_REPO", "../geo", "Geo")

    assert row["state"] == "unknown"


# --- running image ------------------------------------------------------------


def test_running_image_row_is_unknown_when_the_container_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(status, "_run_read_only", lambda *a, **k: _completed(1))

    row = status.running_image_row(
        "map", "c", "지도 API", pinned_revision=MAP_REVISION, pin_trustworthy=True
    )

    assert row["state"] == "unknown"


def test_running_image_row_treats_a_missing_revision_label_as_normal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Geo/Concierge 이미지에는 그 라벨이 아예 없다 — 실패로 처리하면 늘 빨간불이다."""

    monkeypatch.setattr(status, "_run_read_only", lambda *a, **k: _completed(0, IMAGE_ID))

    def raise_contract(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise DeploymentContractError("image has no source revision label")

    monkeypatch.setattr(status, "inspect_c6c_image_source_revision", raise_contract)

    row = status.running_image_row(
        "map", "c", "지도 API", pinned_revision=MAP_REVISION, pin_trustworthy=True
    )

    assert row["state"] == "unknown"
    assert row["image_id"] == IMAGE_ID


def test_running_image_row_never_claims_up_to_date_against_an_untrusted_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """권위 없는 pin과 대조해 '최신 상태입니다'를 찍으면 안 된다."""

    monkeypatch.setattr(status, "_run_read_only", lambda *a, **k: _completed(0, IMAGE_ID))
    monkeypatch.setattr(
        status, "inspect_c6c_image_source_revision", lambda *a, **k: MAP_REVISION
    )

    row = status.running_image_row(
        "map", "c", "지도 API", pinned_revision=MAP_REVISION, pin_trustworthy=False
    )

    assert row["state"] == "unverified_pin"
    assert row["human"]["level"] == "unverified"
    assert row["pinned_revision"] is None


def test_running_image_row_reports_match_and_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status, "_run_read_only", lambda *a, **k: _completed(0, IMAGE_ID))
    monkeypatch.setattr(
        status, "inspect_c6c_image_source_revision", lambda *a, **k: MAP_REVISION
    )

    match = status.running_image_row(
        "map", "c", "지도 API", pinned_revision=MAP_REVISION, pin_trustworthy=True
    )
    drift = status.running_image_row(
        "map", "c", "지도 API", pinned_revision=OTHER_REVISION, pin_trustworthy=True
    )

    assert match["state"] == "match"
    assert match["human"]["text"].startswith("최신 상태입니다")
    assert drift["state"] == "drift"
    assert drift["human"]["text"].startswith("업데이트가 필요합니다")


def test_running_image_probe_injects_a_timeout_bounded_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """기본 docker 조회는 timeout이 없어 daemon이 물리면 스레드를 영구히 문다."""

    monkeypatch.setattr(status, "_run_read_only", lambda *a, **k: _completed(0, IMAGE_ID))
    captured: dict[str, Any] = {}

    def inspect(image_id, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return MAP_REVISION

    monkeypatch.setattr(status, "inspect_c6c_image_source_revision", inspect)

    status.running_image_row(
        "map", "c", "지도 API", pinned_revision=MAP_REVISION, pin_trustworthy=True
    )

    assert callable(captured["runner"])


# --- 계약 drift 행 -------------------------------------------------------------


def test_execution_boundary_never_reads_the_whole_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`{{json .Config}}`를 뜨면 Env가 통째로 들어온다 — DSN과 서명 secret이 거기 있다."""

    formats: list[str] = []

    def run(argv, **kwargs):  # type: ignore[no-untyped-def]
        formats.extend(part for part in argv if part.startswith("--format="))
        field = "Entrypoint" if "Entrypoint" in " ".join(argv) else "Cmd"
        payload = '["/app/docker/api-entrypoint.sh"]' if field == "Entrypoint" else "null"
        return _completed(0, payload)

    monkeypatch.setattr(status, "_run_read_only", run)

    row = status.map_execution_boundary_row()

    assert row["state"] == "match"
    assert all(fmt.endswith(("Entrypoint}}", "Cmd}}")) for fmt in formats)
    assert not any(fmt == "--format={{json .Config}}" for fmt in formats)


def test_execution_boundary_detects_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """이 계약은 실제로 사흘 만에 정반대로 뒤집힌 적이 있다."""

    def run(argv, **kwargs):  # type: ignore[no-untyped-def]
        if "Entrypoint" in " ".join(argv):
            return _completed(0, "null")
        return _completed(0, '["./docker/api-entrypoint.sh"]')

    monkeypatch.setattr(status, "_run_read_only", run)

    row = status.map_execution_boundary_row()

    assert row["state"] == "drift"
    assert row["human"]["level"] == "action_required"


def test_dockerfile_row_refuses_to_judge_a_checkout_that_is_not_the_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """작업 브랜치에서 나온 drift는 '고정된 Dockerfile이 깨졌다'는 뜻이 아니다."""

    monkeypatch.setattr(status, "_run_read_only", lambda *a, **k: _completed(0, OTHER_REVISION))

    row = status.map_dockerfile_structure_row(
        pinned_revision=MAP_REVISION, pin_trustworthy=True
    )

    assert row["state"] == "unknown"
    assert row["head_revision"] == OTHER_REVISION
    assert row["scope"] == "sibling_checkout"


def test_dockerfile_row_is_unknown_without_a_trustworthy_pin() -> None:
    row = status.map_dockerfile_structure_row(pinned_revision=None, pin_trustworthy=False)

    assert row["state"] == "unknown"


@pytest.mark.parametrize(
    "text,expected",
    [
        (f"FROM {PINNED_BASE} AS builder\nRUN x\nFROM {PINNED_BASE} AS runtime\n", "ok"),
        # 부동 태그 — 재구축이 요구하는 immutable base 계약 위반.
        ("FROM python:3.12-slim AS builder\nFROM python:3.12-slim AS runtime\n", "drift"),
        # stage가 3개.
        (
            f"FROM {PINNED_BASE} AS builder\nFROM {PINNED_BASE} AS runtime\n"
            f"FROM {PINNED_BASE} AS extra\n",
            "drift",
        ),
        # 두 stage의 digest가 다르다.
        (
            f"FROM {PINNED_BASE} AS builder\nFROM python@sha256:{'f' * 64} AS runtime\n",
            "drift",
        ),
    ],
)
def test_dockerfile_base_contract_mirrors_the_rebuild_rule(text: str, expected: str) -> None:
    state, _ = status._dockerfile_base_contract(text)

    assert state == expected


# --- 환경 완결성 ---------------------------------------------------------------


def test_environment_card_never_opens_the_real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env`에는 machine secret이 전부 있고 이 카드에 필요한 건 이름뿐이다."""

    opened: list[str] = []
    real_read_bytes = Path.read_bytes

    def spy(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened.append(self.name)
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", spy)

    status.environment_completeness_card()

    assert ".env" not in opened
    assert ".env.example" in opened


def test_environment_card_separates_rebuild_injected_from_actionable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """둘을 합쳐 세면 카드가 영구 빨간불이 되고 사람이 보지 않게 된다."""

    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        "  a:\n"
        "    environment:\n"
        "      X: ${OPERATOR_SUPPLIED:?}\n"
        "      Y: ${SOME_FENCE_DIR:?}\n"
        "      Z: ${DOCUMENTED_ONE:?}\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text("DOCUMENTED_ONE=x\n", encoding="utf-8")
    monkeypatch.setattr(status, "get_compose_path", lambda: str(compose))
    monkeypatch.setattr(status, "get_project_root", lambda: str(tmp_path))

    card = status.environment_completeness_card()

    assert card["missing"] == ["OPERATOR_SUPPLIED"]
    assert card["injected_at_rebuild"] == ["SOME_FENCE_DIR"]
    assert card["state"] == "incomplete"


def test_environment_card_ignores_escaped_dollar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Compose에서 `$$`는 리터럴 `$`다 — 먼저 지우지 않으면 필수 변수로 오인한다."""

    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  a:\n    command: echo $${NOT_A_VAR:?}\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    monkeypatch.setattr(status, "get_compose_path", lambda: str(compose))
    monkeypatch.setattr(status, "get_project_root", lambda: str(tmp_path))

    card = status.environment_completeness_card()

    assert card["required_count"] == 0
    assert card["state"] == "complete"


# --- 요약과 캐시 ---------------------------------------------------------------


def test_summary_prefers_actionable_over_unverified() -> None:
    """확인 불가보다 조치 필요가 먼저다 — 후자에는 누를 수 있는 다음 행동이 있다."""

    rows = [
        {"human": {"level": "unverified", "next_action": "x"}},
        {"human": {"level": "action_required", "next_action": "do-this"}},
        {"human": {"level": "ok", "next_action": ""}},
    ]

    summary = status._summarize(rows)

    assert summary["level"] == "action_required"
    assert summary["next_action"] == "do-this"


def test_a_failing_collector_degrades_one_row_not_the_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """새 실패 모드가 카드 전체를 죽이면 나머지 다섯 행도 함께 잃는다."""

    def explode() -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(status, "read_installer_provenance", explode)
    monkeypatch.setattr(status, "_run_read_only", lambda *a, **k: None)

    payload = status.collect_source_status()

    assert payload["manager"]["state"] == "unknown"
    assert payload["schema"] == status.SOURCE_STATUS_SCHEMA
    assert "summary" in payload


def test_cache_is_reused_and_bypassed_by_force_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def collect() -> dict[str, Any]:
        calls["count"] += 1
        return {"schema": status.SOURCE_STATUS_SCHEMA, "summary": {"level": "ok"}}

    monkeypatch.setattr(status, "_collect_uncached", collect)

    first = status.collect_source_status()
    second = status.collect_source_status()
    third = status.collect_source_status(force_refresh=True)

    assert calls["count"] == 2
    assert first["cached"] is False
    assert second["cached"] is True
    assert third["cached"] is False


def test_cache_is_not_returned_by_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    """캐시 사전을 그대로 주면 라우터·직렬화 단계의 변형이 캐시를 오염시킨다."""

    monkeypatch.setattr(
        status,
        "_collect_uncached",
        lambda: {"schema": status.SOURCE_STATUS_SCHEMA, "rows": [{"state": "ok"}]},
    )

    first = status.collect_source_status()
    first["rows"][0]["state"] = "tampered"
    second = status.collect_source_status()

    assert second["rows"][0]["state"] == "ok"
