"""디스크 사용량 카드 계약 테스트.

이 카드가 잘못된 숫자를 보여 주면 사람이 멀쩡한 이미지를 지우거나, 반대로 디스크가
차는 것을 놓친다. 그래서 "파싱 실패는 추측하지 않고 unknown"을 촘촘히 고정한다.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from kor_travel_docker_manager.services import disk_usage


@pytest.fixture(autouse=True)
def _clear_cache():
    disk_usage.clear_disk_usage_cache()
    yield
    disk_usage.clear_disk_usage_cache()


def _df_output(rows: list[dict[str, Any]]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["docker"],
        returncode=0,
        stdout="\n".join(json.dumps(row) for row in rows),
        stderr="",
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.5GB", int(1.5 * 1024**3)),
        ("512MB", 512 * 1024**2),
        ("2kB", 2048),
        ("0B", 0),
        ("N/A", None),
        ("", None),
        ("not-a-size", None),
        (None, None),
        (123, None),
    ],
)
def test_size_parsing_never_guesses(raw, expected) -> None:
    """잘못된 숫자는 없는 숫자보다 나쁘다."""

    assert disk_usage._parse_size(raw) == expected


def test_reads_docker_df_and_translates_to_plain_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _df_output(
            [
                {
                    "Type": "Images",
                    "TotalCount": "40",
                    "Active": "12",
                    "Size": "30GB",
                    "Reclaimable": "25GB (83%)",
                },
                {
                    "Type": "Build Cache",
                    "TotalCount": "100",
                    "Active": "0",
                    "Size": "5GB",
                    "Reclaimable": "5GB",
                },
            ]
        ),
    )

    payload = disk_usage.read_disk_usage()

    assert payload["state"] == "warn"
    # 원시 바이트가 아니라 "정리하면 얼마나 확보되나"가 운영자가 쓰는 정보다.
    assert "확보 가능" in payload["summary"]["text"]
    assert payload["rows"][0]["label_ko"] == "이미지"
    assert payload["reclaimable_bytes"] == 30 * 1024**3


def test_reclaimable_percentage_suffix_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """docker는 `1.2GB (45%)` 형태로 준다 — 괄호를 그대로 파싱하면 None이 된다."""

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _df_output(
            [{"Type": "Images", "Size": "10GB", "Reclaimable": "1.2GB (45%)"}]
        ),
    )

    payload = disk_usage.read_disk_usage()

    assert payload["rows"][0]["reclaimable_bytes"] == int(1.2 * 1024**3)


def test_small_reclaimable_is_not_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _df_output([{"Type": "Images", "Size": "3GB", "Reclaimable": "1GB"}]),
    )

    payload = disk_usage.read_disk_usage()

    assert payload["state"] == "ok"
    assert payload["summary"]["next_action"] == ""


def test_daemon_failure_is_unknown_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=["docker"], returncode=1, stdout="", stderr="x"),
    )

    payload = disk_usage.read_disk_usage()

    assert payload["state"] == "unknown"
    assert payload["reclaimable_bytes"] is None


def test_spawn_failure_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*a, **k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", explode)

    assert disk_usage.read_disk_usage()["state"] == "unknown"


def test_never_invokes_prune(monkeypatch: pytest.MonkeyPatch) -> None:
    """정리는 파괴적이라 CLI 전용이다 — 카드는 명령만 알려 준다."""

    commands: list[list[str]] = []

    def capture(argv, **kwargs):
        commands.append(list(argv))
        return _df_output([{"Type": "Images", "Size": "30GB", "Reclaimable": "25GB"}])

    monkeypatch.setattr(subprocess, "run", capture)

    payload = disk_usage.read_disk_usage()

    assert all("prune" not in command for command in commands)
    assert "prune" in payload["summary"]["next_action"]


def test_result_is_cached_and_bypassed_by_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def counted(*a, **k):
        calls["count"] += 1
        return _df_output([{"Type": "Images", "Size": "1GB", "Reclaimable": "0B"}])

    monkeypatch.setattr(subprocess, "run", counted)

    first = disk_usage.read_disk_usage()
    second = disk_usage.read_disk_usage()
    third = disk_usage.read_disk_usage(force_refresh=True)

    assert calls["count"] == 2
    assert first["cached"] is False
    assert second["cached"] is True
    assert third["cached"] is False


def test_malformed_json_line_does_not_discard_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    def mixed(*a, **k):
        return subprocess.CompletedProcess(
            args=["docker"],
            returncode=0,
            stdout='{"Type": "Images", "Size": "2GB", "Reclaimable": "1GB"}\nnot-json\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", mixed)

    payload = disk_usage.read_disk_usage()

    assert payload["state"] == "ok"
    assert len(payload["rows"]) == 1
