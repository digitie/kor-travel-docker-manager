"""실제 수집기 AST와 합성 registry/Docker 대역으로 전후 작업량을 비교한다.

실행: python backend/tests/benchmark_metrics_observation.py
운영 Docker·DB·설정·SDK를 읽지 않으며 생성자 비용은 측정에서 제외한다.
"""

import asyncio
import datetime
import gc
import json
import statistics
import subprocess
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

from test_metrics_observation_budget import Container, ResolvingMapping, load_collector

ROOT = Path(__file__).parents[2]
BASE = "8bb8f996cf118153561c157858c2e6fbc1405c4b"
PATH = "backend/src/kor_travel_docker_manager/services/metrics_collector.py"


class FixedDatetime:
    @staticmethod
    def now(tz):
        return datetime.datetime(2026, 10, 17, tzinfo=tz)


def prepare(source, count, mode):
    ns = load_collector(source)
    specs = {
        f"app-{i}": {"name": f"container-{i}", "role": "backend", "expected_ports": [12345]}
        for i in range(count)
    }
    mapping = ResolvingMapping(specs)
    ns["MANAGED_CONTAINERS"] = mapping
    collector = ns["MetricsCollector"]()
    mapping.resolves = mapping.spec_visits = 0
    ns["time"] = SimpleNamespace(time=lambda: 1234.0, monotonic=lambda: 1234.0)
    ns["datetime"] = SimpleNamespace(datetime=FixedDatetime, UTC=datetime.UTC)
    containers = {spec["name"]: Container(mode) for spec in specs.values()}
    collector.set_docker_client_provider(
        lambda: SimpleNamespace(containers=SimpleNamespace(get=containers.__getitem__))
    )
    return collector, mapping, containers


async def cycles(collector, count):
    for _ in range(count):
        await collector.collect_metrics()


def measure(source, count, mode, *, allocation=False, iterations=100):
    collector, mapping, containers = prepare(source, count, mode)
    gc.collect()
    if allocation:
        tracemalloc.start()
    cpu = time.process_time()
    wall = time.perf_counter()
    asyncio.run(cycles(collector, iterations))
    wall = time.perf_counter() - wall
    cpu = time.process_time() - cpu
    peak = tracemalloc.get_traced_memory()[1] if allocation else None
    if allocation:
        tracemalloc.stop()
    return {
        "cpu_seconds": cpu, "wall_seconds": wall, "tracemalloc_peak_bytes": peak,
        "registry_resolves": mapping.resolves, "spec_visits": mapping.spec_visits,
        "image_reads": sum(c.image_reads for c in containers.values()),
    }, collector._container_observations


def main():
    baseline = subprocess.run(
        ["git", "show", f"{BASE}:{PATH}"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    current = (ROOT / PATH).read_text()
    results = []
    for count in (16, 64):
        for mode in ("exited", "running"):
            pairs = {"baseline": [], "candidate": []}
            for repeat in range(5):
                # OS 캐시/순서 편향을 줄이되 환경 통제된 운영 실측으로 확대하지 않는다.
                order = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
                outputs = {}
                for name in order:
                    row, outputs[name] = measure(
                        baseline if name == "baseline" else current, count, mode
                    )
                    pairs[name].append(row)
                assert outputs["baseline"] == outputs["candidate"]
            memory = {}
            for name, source in (("baseline", baseline), ("candidate", current)):
                memory[name], _ = measure(source, count, mode, allocation=True)
            results.append({
                "containers": count, "state": mode, "cycles_per_sample": 100, "pairs": 5,
                "output_equal": True, "samples": pairs,
                "cpu_median_seconds": {
                    name: statistics.median(row["cpu_seconds"] for row in rows)
                    for name, rows in pairs.items()
                },
                "memory_separate_pass": memory,
            })
    print(json.dumps({
        "baseline": BASE, "model": "실제 collector AST + 합성 전체 mapping copy + fake Docker/stats/save",
        "limits": "생성자 제외; 실제 host 검증·SDK·IO 비용/운영 CPU·RSS 개선은 측정하지 않음",
        "results": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
