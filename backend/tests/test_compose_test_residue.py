"""`_compose_test_residue.sweep_dead_runs` — 죽은 실행의 격리 프로젝트만 회수한다(대역 docker로)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from _compose_test_residue import sweep_dead_runs


class _FakeDocker:
    def __init__(self, projects: dict[str, list[str]]) -> None:
        self.projects = projects  # project -> container ids
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        args = argv[1:]
        if "--format" in args:
            text = "\n".join(self.projects) if args[0] == "ps" else ""
            return subprocess.CompletedProcess(argv, 0, stdout=text, stderr="")
        if "--filter" in args:
            project = args[args.index("--filter") + 1].split("=", 2)[2]
            ids = self.projects.get(project, []) if args[0] == "ps" else []
            return subprocess.CompletedProcess(argv, 0, stdout="\n".join(ids), stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def test_only_dead_runs_with_the_prefix_are_removed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "4242").mkdir()  # 살아 있는 실행
    docker = _FakeDocker({
        "ktdm-dagsterplane-4242-aaaa": ["live1"],
        "ktdm-dagsterplane-1111-bbbb": ["dead1", "dead2"],
        "ktdm-readiness-1111-cccc": ["other-prefix"],
        "kor-travel-docker-manager": ["production"],
    })
    removed = sweep_dead_runs("ktdm-dagsterplane-", run=docker, proc=proc)
    assert removed == ["ktdm-dagsterplane-1111-bbbb"]
    removals = [c for c in docker.calls if c[1] == "rm"]
    assert removals == [["docker", "rm", "--force", "--volumes", "dead1", "dead2"]]
    assert ["docker", "image", "rm", "--force", "ktdm-dagsterplane-1111-bbbb:it"] in docker.calls
    flat = " ".join(" ".join(c) for c in docker.calls)
    assert "live1" not in flat and "production" not in flat and "other-prefix" not in flat


def test_nothing_happens_without_proc(tmp_path: Path) -> None:
    docker = _FakeDocker({"ktdm-dagsterplane-1111-bbbb": ["dead1"]})
    assert sweep_dead_runs("ktdm-dagsterplane-", run=docker, proc=tmp_path / "absent") == []
    assert docker.calls == []


import pytest


@pytest.mark.parametrize(("env", "value"), [("DOCKER_HOST", "tcp://10.0.0.5:2376"), ("DOCKER_HOST", "ssh://odroid"),
                                            ("DOCKER_CONTEXT", "remote")])
def test_a_remote_daemon_or_context_is_not_swept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: str, value: str) -> None:
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    monkeypatch.setenv(env, value)
    docker = _FakeDocker({"ktdm-dagsterplane-1111-bbbb": ["dead1"]})
    assert sweep_dead_runs("ktdm-dagsterplane-", run=docker, proc=proc) == []
    assert docker.calls == []


def test_a_local_unix_socket_is_still_swept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    docker = _FakeDocker({"ktdm-dagsterplane-1111-bbbb": ["dead1"]})
    assert sweep_dead_runs("ktdm-dagsterplane-", run=docker, proc=proc) == ["ktdm-dagsterplane-1111-bbbb"]


def test_non_ascii_digits_are_not_pids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    docker = _FakeDocker({"ktdm-dagsterplane-١٢-bbbb": ["odd"]})  # 아랍 숫자 — str.isdigit()는 참
    assert sweep_dead_runs("ktdm-dagsterplane-", run=docker, proc=proc) == []


def test_a_docker_timeout_warns_and_does_not_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)

    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 120)

    with pytest.warns(UserWarning, match="timed out"):
        assert sweep_dead_runs("ktdm-dagsterplane-", run=hang, proc=proc) == []
