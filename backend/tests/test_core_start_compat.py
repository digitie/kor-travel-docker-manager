"""실제 구형 Compose start 옵션과 지연 health 응답의 core 회귀."""

import subprocess
from types import SimpleNamespace

import pytest
from test_transport_core_candidate import apply_core
from test_transport_core_candidate import core_case as core_case
from test_transport_core_candidate import scenario as scenario

from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_core_candidate as core


@pytest.mark.parametrize("timeout", [False, True])
def test_transport_legacy_compose_start_then_bounded_health_poll(core_case, monkeypatch, timeout):
    s = core_case
    original_mutation = core.frozen._run_mutation
    original_verify = core.frozen._verify
    pending = []
    sleeps = []
    def old_compose(argv, **kwargs):
        if "start" in argv and "--wait" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "unknown flag: --wait"), []
        return original_mutation(argv, **kwargs)
    def delayed_health(manifests, client):
        if "start" in s.steps and (timeout or len(pending) < 2):
            pending.append(True)
            return {"transport": {"result": "starting"}}
        return original_verify(manifests, client)
    ticks = iter([0, 1000000])
    clock = SimpleNamespace(monotonic=(lambda: next(ticks)) if timeout else (lambda: 0), sleep=sleeps.append)
    monkeypatch.setattr(core.frozen, "_run_mutation", old_compose)
    monkeypatch.setattr(core.frozen, "_verify", delayed_health)
    monkeypatch.setattr(core, "time", clock, raising=False)
    result = apply_core(s)
    if timeout:
        assert not result["success"] and result["phase"] == "start"
        assert release.read_current_release("transport", root=s.root) == s.current
        assert pending
    else:
        assert result["success"], result
        assert len(pending) == 2 and sleeps == [1, 1]
    assert all("--wait" not in argv for argv, _ in s.calls if "start" in argv)
