"""실제 legacy Map의 source 라벨 부재를 authoritative pair에 한정한다."""
from __future__ import annotations

import copy
import json

import pytest
import test_service_release_pinvi_web_candidate as previous

from kor_travel_docker_manager.services import deploy_status
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services.pinned_runtime_release import (
    PinnedRuntimeRelease,
    canonical_pinset_sha256,
    source_specs_for,
)

scenario = previous.scenario
pinvi = previous.pinvi
legacy_map = previous.legacy_map


@pytest.fixture
def unlabelled_pair(legacy_map, monkeypatch):
    s = legacy_map
    labels = s.metadata[previous.paired.OLD]["Config"]["Labels"]
    del labels["org.opencontainers.image.source"]
    specs = source_specs_for(map_revision=s.status.map_revision, pinvi_revision="0" * 40)
    s.pin = PinnedRuntimeRelease(
        version=5, sources=specs, pinset_sha256=canonical_pinset_sha256(version=5, sources=specs))
    s.status = deploy_status.DeployStatus(
        state="committed", run_id="00000000-0000-0000-0000-000000000001",
        started_at="2026-11-13T00:00:00Z", committed_at="2026-11-13T00:01:00Z",
        manager_revision=s.sha, map_revision=specs[0].revision, pinvi_revision=specs[1].revision,
        pinset_sha256=s.pin.pinset_sha256, images=dict(s.status.images),
        schema_heads={"map_application": "fixture", "pinvi": "fixture"},
        databases={"map_application": deploy_status.DeployedDatabase("fixture_map", 1, "1"),
                   "pinvi": deploy_status.DeployedDatabase("fixture_pinvi", 2, "1")})
    s.record = s.root / "baseline"
    deploy_status.write_deploy_status(s.record, s.status)
    def baseline(*args):
        raw = release._read_file(s.record)
        return (s.record, raw, {"pin": s.pin.to_payload(), "environment": {}},
                deploy_status._status_from_payload(json.loads(raw)))
    s.baseline_reader = baseline
    monkeypatch.setattr(s.module, "_baseline", baseline)
    monkeypatch.setattr(s.module, "current_pinned_runtime_release", lambda: s.pin)
    return s


def test_actual_unlabelled_map_shape_uses_trusted_pair_for_public_dryrun(unlabelled_pair):
    s = unlabelled_pair
    before = copy.deepcopy(s.records)
    raw = s.record.read_bytes()
    result = previous.apply(s, execute=False)
    assert result["success"] and result["result"] == "planned"
    assert result["legacy_map_unchanged"] is True
    assert s.records == before and not previous.mutations(s)
    assert s.record.read_bytes() == raw
    assert "org.opencontainers.image.source" not in s.metadata[previous.paired.OLD]["Config"]["Labels"]
    assert not (s.root / "current/map").exists()
    assert not (s.root / "current/map").is_symlink()


def test_unlabelled_map_apply_does_not_relabel_or_publish_map(unlabelled_pair):
    s = unlabelled_pair
    before = copy.deepcopy(s.records)
    metadata = copy.deepcopy(s.metadata[previous.paired.OLD])
    pair = s.record.read_bytes()
    leaves = sorted(p.name for p in s.root.glob("map-*"))
    result = previous.apply(s)
    assert result["success"] and result["result"] == "healthy"
    assert len(previous.mutations(s)) == 1 and previous.mutations(s)[0][0][-1] == previous.WEB
    assert {k: v for k, v in s.records.items() if k != previous.WEB} == {
        k: v for k, v in before.items() if k != previous.WEB}
    assert s.metadata[previous.paired.OLD] == metadata and s.record.read_bytes() == pair
    assert sorted(p.name for p in s.root.glob("map-*")) == leaves
    assert not (s.root / "current/map").exists()
    assert not (s.root / "current/map").is_symlink()


@pytest.mark.parametrize("value", ["", None, 0, "not-a-url", previous.URL,
                                   "https://github.com/digitie/kor-travel-map"])
def test_present_source_label_never_uses_missing_label_exception(unlabelled_pair, value):
    s = unlabelled_pair
    s.metadata[previous.paired.OLD]["Config"]["Labels"]["org.opencontainers.image.source"] = value
    with pytest.raises(release.ServiceReleaseError):
        previous.apply(s)
    assert not previous.mutations(s)


@pytest.mark.parametrize("kind", ["missing", "changed", "uncommitted", "map-revision",
                                 "pinvi-revision", "wrong-image", "pinset", "unknown-role",
                                 "symlink", "unsafe"])
def test_missing_or_untrusted_record_cannot_replace_oci_source(unlabelled_pair, monkeypatch, kind):
    s = unlabelled_pair
    # 실제 baseline 읽기 뒤 파일이 교체/삭제된 시점의 race를 재현한다.
    cached = s.baseline_reader()
    monkeypatch.setattr(s.module, "_baseline", lambda *args: cached)
    payload = s.status.to_payload()
    if kind == "missing":
        s.record.unlink()
    elif kind == "symlink":
        target = s.root / "other-record"
        target.write_bytes(s.record.read_bytes())
        s.record.unlink()
        s.record.symlink_to(target)
    elif kind == "unsafe":
        s.record.chmod(0o666)
    else:
        if kind == "changed":
            payload["manager_revision"] = "9" * 40
        elif kind == "uncommitted":
            payload["state"] = "in_progress"
            payload["committed_at"] = None
        elif kind == "map-revision":
            payload["map_revision"] = "9" * 40
        elif kind == "pinvi-revision":
            payload["pinvi_revision"] = "9" * 40
        elif kind == "wrong-image":
            payload["images"][previous.MAP_UI] = previous.MAP_CURRENT
        elif kind == "pinset":
            payload["pinset_sha256"] = "9" * 64
        else:
            del payload["images"][previous.MAP_UI]
        s.record.write_text(json.dumps(payload))
    with pytest.raises(release.ServiceReleaseError):
        previous.apply(s)
    assert not previous.mutations(s)
    assert release.read_current_release("pinvi", root=s.root) == s.current


@pytest.mark.parametrize("kind", ["pair", "pin", "source", "pointer", "pid", "environment"])
def test_unlabelled_legacy_provenance_is_rechecked_before_publication(unlabelled_pair, kind):
    s = unlabelled_pair
    def change():
        if kind == "pair":
            payload = s.status.to_payload()
            payload["map_revision"] = "9" * 40
            s.record.write_text(json.dumps(payload))
        elif kind == "pin":
            specs = source_specs_for(map_revision=s.status.map_revision, pinvi_revision="9" * 40)
            s.pin = PinnedRuntimeRelease(
                version=5, sources=specs,
                pinset_sha256=canonical_pinset_sha256(version=5, sources=specs))
        elif kind == "source":
            s.metadata[previous.paired.OLD]["Config"]["Labels"]["org.opencontainers.image.source"] = ""
        elif kind == "pointer":
            (s.root / "current/map").symlink_to(s.root / "missing")
        elif kind == "pid":
            s.records[previous.MAP_UI]["State"]["Pid"] += 1
        else:
            s.records[previous.MAP_UI]["Config"]["Env"].append("OBSERVED=changed")
    s.on_mutation = change
    result = previous.apply(s)
    assert not result["success"] and len(previous.mutations(s)) == 1
    assert release.read_current_release("pinvi", root=s.root) == s.current


@pytest.mark.parametrize("kind", ["absent", "empty", "null", "wrong"])
def test_selected_new_pinvi_source_label_remains_mandatory(unlabelled_pair, kind):
    s = unlabelled_pair
    labels = s.metadata[previous.paired.NEW]["Config"]["Labels"]
    if kind == "absent":
        del labels["org.opencontainers.image.source"]
    else:
        labels["org.opencontainers.image.source"] = {"empty": "", "null": None, "wrong": previous.paired.SOURCE_URL}[kind]
    with pytest.raises(release.ServiceReleaseError):
        previous.apply(s)
    assert not previous.mutations(s)
