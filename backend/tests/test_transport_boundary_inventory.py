"""Transport common의 한 경계 관측과 다음 경계의 거부 계약."""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_transport_core_candidate as transport_tests
from docker.models.containers import ContainerCollection
from test_service_release_candidate import all_label_match
from test_transport_core_candidate import apply_core
from test_transport_core_candidate import scenario as scenario

from kor_travel_docker_manager.services import service_release_component_candidate as component
from kor_travel_docker_manager.services import service_release_core_candidate as core
from kor_travel_docker_manager.services import transport_core_state as state

core_case = transport_tests.core_case


def test_common_54_container_budget_is_one_fresh_list_per_boundary(core_case, monkeypatch):
    s = core_case
    for index in range(54 - len(s.records)):
        record = copy.deepcopy(s.records["db"])
        record["Id"] = f"unselected-{index}"
        record["Config"]["Labels"] = {}
        s.records[f"unselected-{index}"] = record
    counts = {"lists": 0, "inspects": 0}
    starts, boundaries, snapshots = [], [], []

    def listing(**kwargs):
        assert kwargs["all"] is True
        counts["lists"] += 1
        records = list(s.records.values())
        if filters := kwargs.get("filters"):
            records = [record for record in records if all_label_match(record, filters["label"])]
        return [{"Id": record["Id"]} for record in records]

    def inspect_container(cid):
        counts["inspects"] += 1
        return copy.deepcopy(next(record for record in s.records.values() if record["Id"] == cid))

    monkeypatch.setattr(s.client.api, "containers", listing, raising=False)
    monkeypatch.setattr(s.client.api, "inspect_container", inspect_container)
    s.client.containers = ContainerCollection(s.client)
    original_lease = state.check_lease

    def lease(*args):
        original_lease(*args)
        starts.append(counts.copy())

    monkeypatch.setattr(state, "check_lease", lease)
    original_environment = component._preserve_environment

    def preserve(*args, **kwargs):
        bind, check, values = original_environment(*args, **kwargs)
        role = args[2]

        def unchanged(records=None):
            if records is None:
                check()
            else:
                check(records)
            snapshots.append(records)
            if role == sorted(core.RUNTIME)[-1]:
                boundaries.append({key: counts[key] - starts[-1][key] for key in counts})
                assert all(rows is records for rows in snapshots[-3:])
                if records is not None:
                    assert len(records) == 54
        return bind, unchanged, values

    monkeypatch.setattr(component, "_preserve_environment", preserve)
    result = apply_core(s)
    assert result["success"], result
    assert len(boundaries) >= 5
    assert all(row == {"lists": 1, "inspects": 54} for row in boundaries), boundaries
    assert all(snapshots[index] is not snapshots[index - 3]
               for index in range(3, len(snapshots), 3))


@pytest.mark.parametrize("change", ["nonselected", "removed-nonselected", "environment",
                                    "duplicate-id", "missing-id", "nonstring-id", "pins", "protected-file"])
def test_next_common_boundary_refuses_drift_and_incomplete_identity(core_case, monkeypatch, change):
    s = core_case

    def mutate():
        if change == "nonselected":
            s.records["db"]["RestartCount"] += 1
        elif change == "removed-nonselected":
            del s.records["db"]
        elif change == "environment":
            s.records["backend"]["Config"]["Env"].append("UNAPPROVED=changed")
        elif change == "duplicate-id":
            s.records["duplicate"] = copy.deepcopy(s.records["db"])
        elif change == "missing-id":
            s.records["db"].pop("Id")
        elif change == "nonstring-id":
            s.records["db"]["Id"] = 42
        elif change == "pins":
            monkeypatch.setattr(core.candidate, "current_pinned_runtime_release",
                                lambda: SimpleNamespace(to_payload=lambda: {"pins": "changed"}))
        else:
            Path(core.get_env_path()).write_text("changed after snapshot")

    s.hooks["snapshot"] = mutate
    result = apply_core(s)
    assert result["success"] is False
    assert result["error_type"] == "ServiceReleaseError"
    expected = "비선택 runtime" if change in {"nonselected", "removed-nonselected"} else (
        "effective" if change == "environment" else "전체 컨테이너 identity" if change in {
            "duplicate-id", "missing-id", "nonstring-id"} else "보호 파일 또는 pin")
    assert expected in result["message"]
    assert s.steps == ["factory", "snapshot"] and not s.calls
    assert core.release.read_current_release("transport", root=s.root) == s.current
