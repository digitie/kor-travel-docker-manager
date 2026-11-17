"""대규모 Dagster state 스캔의 예산과 기존 읽기 probe 경계를 검증한다."""

import json
import subprocess
import sys

from kor_travel_docker_manager.services import service_release_core_candidate as core
from kor_travel_docker_manager.services import transport_core_state as state


def test_state_fingerprint_allows_slow_scan_without_losing_metadata(monkeypatch, tmp_path):
    (tmp_path / "checkpoint.json").write_text('{"offset": 17}')
    (tmp_path / "checkpoint.json").chmod(0o600)
    (tmp_path / "checkpoint-link").symlink_to("checkpoint.json")
    calls = []

    def run_mutation(command, **kwargs):
        calls.append((command, kwargs))
        # 실제 운영에서 전체 스캔이 약72초였다. 긴 sleep 없이 기존60초 실패를 재현한다.
        if kwargs["timeout"] < 72:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        script = command[-1].replace("Path('/opt/dagster/state')", f"Path({str(tmp_path)!r})")
        completed = subprocess.run(
            [sys.executable, "-I", "-c", script],
            capture_output=True, text=True, check=False, timeout=10,
        )
        return completed, []

    monkeypatch.setattr(core.frozen, "_run_mutation", run_mutation)
    assert core._fingerprint("existing-code-server", 17) == state.scan_state(tmp_path)
    command, kwargs = calls[0]
    assert command[:6] == ["docker", "exec", "existing-code-server", "python", "-I", "-c"]
    assert kwargs["timeout"] >= 180
    assert kwargs["additional_fds"] == (17,)


def test_other_read_probes_keep_sixty_second_budget(monkeypatch):
    calls = []

    def run_mutation(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, json.dumps({"healthy": True}), ""), []

    monkeypatch.setattr(core.frozen, "_run_mutation", run_mutation)
    assert core._probe("existing-code-server", "read_only_probe()", 19) == {"healthy": True}
    assert calls[0]["timeout"] == 60
    assert calls[0]["additional_fds"] == (19,)
