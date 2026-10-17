"""cAdvisor의 현재운영 수집 범위를 source에 결박한다. 실제 Docker를 실행하지 않는다."""

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_DISABLED = (
    "percpu,sched,tcp,udp,disk,diskIO,hugetlb,referenced_memory,cpu_topology,resctrl"
    ",memory_numa,advtcp,process,cpuset"
)


# v0.52.1 cmd/cadvisor.go의 ignoreMetrics 기본집합. 명시 Set은 추가가 아니라 교체다.
_DEFAULT_DISABLED = {
    "memory_numa", "tcp", "udp", "advtcp", "sched", "process",
    "hugetlb", "referenced_memory", "cpu_topology", "resctrl", "cpuset",
}


def _command() -> list[str]:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]["cadvisor"]["command"]


def test_collection_budget_preserves_original_args_and_has_no_conflicting_flags() -> None:
    command = _command()
    additions = ["--housekeeping_interval=${CADVISOR_HOUSEKEEPING_INTERVAL:-15s}", "--disable_metrics=" + _DISABLED]
    assert all(command.count(argument) == 1 for argument in additions)
    assert [argument for argument in command if argument not in additions] == [
        "--logtostderr",
        "--docker_only=true",
        "--port=${CADVISOR_PORT:-12103}",
    ]
    options = [argument.split("=", 1)[0] for argument in command]
    assert len(options) == len(set(options)), "중복 flag의 마지막 값이 예산을 덮으면 안 된다"


def test_only_the_declared_metric_families_are_disabled() -> None:
    disabled = next(argument.split("=", 1)[1] for argument in _command() if argument.startswith("--disable_metrics="))
    families = disabled.split(",")
    assert disabled == _DISABLED and len(families) == len(set(families))
    # cpu/memory 전체 family를 끄는 설정과 개별 비용 항목 제외는 구분한다.
    assert not {"cpu", "memory"} & set(families)


def test_explicit_metrics_retain_every_default_disabled_family() -> None:
    disabled = next(argument.split("=", 1)[1] for argument in _command() if argument.startswith("--disable_metrics="))
    assert _DEFAULT_DISABLED <= set(disabled.split(",")), "명시 목록이 기본 off 항목을 다시 켜면 안 된다"
