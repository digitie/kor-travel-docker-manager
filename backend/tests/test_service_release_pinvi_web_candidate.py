"""Pinvi web만 적용하고 검증된 Map current와 paired 비선택 기록을 보존한다."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
import test_service_release_candidate as paired

from kor_travel_docker_manager import cli
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services.registry import load_targets_config

scenario = paired.scenario
WEB = "pinvi-web"
API = "pinvi-api"
CS = "pinvi-dagster-code-server"
MAP_UI = "kor-travel-map-ui"
MAP_API = "kor-travel-map-api"
MAP_CS = "kor-travel-map-dagster-code-server"
PINVI_OLD = "sha256:" + "4" * 64
MAP_CURRENT = "sha256:" + "d" * 64
URL = "https://github.com/digitie/pinvi.git"
PUBLIC = {
    "NEXT_PUBLIC_PINVI_API_URL": "https://api.example.invalid",
    "NEXT_PUBLIC_VWORLD_API_KEY": "fixture-public",
    "NEXT_PUBLIC_GRAFANA_URL": "https://metrics.example.invalid",
}


@pytest.fixture
def pinvi(scenario, monkeypatch):
    s = scenario
    # Existing Map component current legitimately advanced after the old pair receipt.
    s.metadata[MAP_CURRENT] = copy.deepcopy(s.metadata[paired.OLD])
    s.metadata[MAP_CURRENT]["Id"] = MAP_CURRENT
    s.metadata[MAP_CURRENT]["Config"]["Labels"]["org.opencontainers.image.revision"] = "1" * 40
    s.records[MAP_UI]["Image"] = MAP_CURRENT
    s.map_current = release.adopt_release(s.profile, root=s.root, client=s.client, replace_current=True)
    for old, new in [("api", API), ("dagster", CS)]:
        s.records[new] = s.records.pop(old)
        s.records[new]["Id"] = "cid-" + new
        s.records[new]["Config"]["Labels"]["com.docker.compose.service"] = new
    for name in [MAP_API, MAP_CS, WEB]:
        record = copy.deepcopy(s.records[API])
        record["Id"] = "cid-" + name
        record["Config"]["Labels"]["com.docker.compose.service"] = name
        s.records[name] = record
    s.records[WEB]["Image"] = PINVI_OLD
    old_env = {**PUBLIC, "PINVI_SOURCE_REVISION": "0" * 40,
               "NODE_ENV": "production", "PASSWORD": "fixture$secret"}
    new_env = {**old_env, "PINVI_SOURCE_REVISION": s.sha}
    s.records[WEB]["Config"]["Env"] = [f"{k}={v}" for k, v in old_env.items()]
    s.metadata[PINVI_OLD] = copy.deepcopy(s.metadata[paired.OLD])
    s.metadata[PINVI_OLD]["Id"] = PINVI_OLD
    for value, env in [(PINVI_OLD, old_env), (paired.NEW, new_env)]:
        s.metadata[value]["Config"]["Labels"]["org.opencontainers.image.source"] = URL
        s.metadata[value]["Config"]["Env"] = [f"{k}={v}" for k, v in env.items()]
    paired.git(s.source, "remote", "set-url", "origin", URL)
    group = copy.deepcopy(s.profile["groups"]["ui"])
    group["runtime_services"] = [WEB]
    s.profile = {"service": "pinvi", "groups": {"ui": group}}
    s.config["targets"]["pinvi"] = {"host": "test", "containers": [WEB],
                                   "ui_candidate": {"role": "pinvi-web", "source_url": URL}}
    s.config["containers"][WEB] = {"compose_service": WEB, "role": "pinvi-web"}
    s.config["service_releases"]["pinvi"] = {"groups": {"ui": {
        **{k: v for k, v in group.items() if k != "oneshot_images"}, "oneshot_services": []}}}
    s.current = release.adopt_release(s.profile, root=s.root, client=s.client)
    monkeypatch.setattr(paired, "UI", WEB)
    m = paired.prepare(s)
    slots = {"map_api": MAP_API, "map_ui": MAP_UI, "map_dagster": MAP_CS,
             "map_dagster_daemon": None, "pinvi_api": API, "pinvi_web": WEB, "pinvi_dagster": CS}
    topology = SimpleNamespace(runtime_services=[x for x in slots.values() if x],
                               retired_services=(), service=slots.get,
                               families={"map": SimpleNamespace(carrier=MAP_CS),
                                         "pinvi": SimpleNamespace(carrier=CS)})
    monkeypatch.setattr(m, "runtime_topology", lambda: topology)
    s.status = SimpleNamespace(images={name: paired.OTHER for name in topology.runtime_services})
    s.status.images.update({WEB: PINVI_OLD, MAP_UI: paired.OLD})
    path = s.root / "baseline"
    monkeypatch.setattr(m, "_baseline", lambda *args: (
        path, path.read_bytes(), {"pin": "unchanged"}, s.status))
    s.resolved["services"] = {
        name: {"image": record["Image"], "environment": {"PASSWORD": "fixture$secret"}}
        for name, record in s.records.items()}
    original_run = m.frozen._run_mutation
    def run(argv, **kwargs):
        result = original_run(argv, **kwargs)
        definition = json.loads(kwargs["input"])["services"][WEB]
        s.records[WEB]["Config"]["Env"] = [
            f"{key}={value.replace('$$', '$')}" for key, value in definition["environment"].items()]
        if getattr(s, "after_environment", None):
            s.after_environment()
        return result
    monkeypatch.setattr(m.frozen, "_run_mutation", run)
    s.module = m
    return s


def apply(s, **changes):
    values = dict(service="pinvi", group="ui", image_id=paired.NEW, source_sha=s.sha,
                  source_clone=s.source, root=s.root, config=s.config,
                  client_factory=s.factory, execute=True)
    values.update(changes)
    return s.module.apply_ui_candidate(**values)


def mutations(s):
    return [x for x in s.calls if isinstance(x, tuple) and "up" in x[0]]


def test_pinvi_catalog_declares_actual_web_without_pair_or_map_scope(monkeypatch):
    from kor_travel_docker_manager import service_release_cli as entry
    config = load_targets_config()
    monkeypatch.setattr(entry, "current_host", lambda config: "n150")
    assert entry._declaration("pinvi", config)["groups"]["ui"]["runtime_services"] == [WEB]
    assert config["targets"]["pinvi"]["ui_candidate"] == {"role": "pinvi-web", "source_url": URL}


def test_selected_web_only_preserves_advanced_map_current_pair_and_other_runtime(pinvi):
    s = pinvi
    before = copy.deepcopy(s.records)
    old_pair = copy.deepcopy(s.status.images)
    result = apply(s)
    assert result["success"] is True
    command, kwargs = mutations(s)[0]
    assert command[command.index("up"):] == [
        "up", "-d", "--no-deps", "--no-build", "--pull", "never",
        "--force-recreate", "--wait", "--wait-timeout", "120", WEB]
    materialized = json.loads(kwargs["input"])["services"]
    assert materialized[WEB]["image"] == paired.NEW
    assert materialized[MAP_UI]["image"] == MAP_CURRENT
    assert {k: v for k, v in s.records.items() if k != WEB} == {
        k: v for k, v in before.items() if k != WEB}
    assert release.read_current_release("map", root=s.root) == s.map_current
    assert s.status.images == old_pair
    assert release.read_current_release("pinvi", root=s.root)["images"][WEB]["id"] == paired.NEW
    assert "fixture$secret" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["unregistered", "wrong-role", "wrong-runtime", "unregistered-target"])
def test_wrong_registration_rejects_before_docker(pinvi, kind):
    s = pinvi
    if kind == "unregistered":
        del s.config["targets"]["pinvi"]["ui_candidate"]
    elif kind == "wrong-role":
        s.config["targets"]["pinvi"]["ui_candidate"]["role"] = "map-ui"
    elif kind == "wrong-runtime":
        s.config["containers"][WEB]["compose_service"] = API
    else:
        del s.config["service_releases"]["pinvi"]
    with pytest.raises(release.ServiceReleaseError):
        apply(s)
    assert "factory" not in s.calls


@pytest.mark.parametrize("kind", ["map-live-drift", "map-current-missing", "web-current-drift",
                                 "map-current-source", "paired-api-drift", "paired-cs-drift"])
def test_stale_or_uncommitted_current_is_not_adopted(pinvi, kind):
    s = pinvi
    if kind == "map-live-drift":
        s.records[MAP_UI]["Image"] = paired.OLD
    elif kind == "map-current-missing":
        (s.root / "current/map").unlink()
    elif kind == "web-current-drift":
        s.records[WEB]["Image"] = paired.NEW
    elif kind == "map-current-source":
        s.metadata[MAP_CURRENT]["Config"]["Labels"]["org.opencontainers.image.revision"] = "2" * 40
    else:
        s.records[API if kind == "paired-api-drift" else CS]["Image"] = paired.NEW
    with pytest.raises(release.ServiceReleaseError):
        apply(s)
    assert not mutations(s)


@pytest.mark.parametrize("kind", ["source", "revision", "baked-public", "runtime-public"])
def test_oci_and_public_input_mismatch_reject_before_mutation(pinvi, kind):
    s = pinvi
    config = s.metadata[paired.NEW]["Config"]
    if kind == "source":
        config["Labels"]["org.opencontainers.image.source"] = paired.SOURCE_URL
    elif kind == "revision":
        config["Labels"]["org.opencontainers.image.revision"] = "3" * 40
    elif kind == "baked-public":
        config["Env"] = [x.replace("fixture-public", "different") for x in config["Env"]]
    else:
        s.records[WEB]["Config"]["Env"] = [
            x.replace("fixture-public", "different") for x in s.records[WEB]["Config"]["Env"]]
    with pytest.raises(release.ServiceReleaseError):
        apply(s)
    assert not mutations(s)


@pytest.mark.parametrize("kind", ["map", "api", "cs", "pair", "map-pointer"])
def test_post_mutation_nonselected_change_refuses_publication(pinvi, kind):
    s = pinvi
    def change():
        if kind == "pair":
            (s.root / "baseline").write_bytes(b"changed")
        elif kind == "map-pointer":
            (s.root / "current/map").unlink()
        else:
            s.records[{"map": MAP_UI, "api": API, "cs": CS}[kind]]["RestartCount"] += 1
    s.on_mutation = change
    result = apply(s)
    assert not result["success"]
    assert release.read_current_release("pinvi", root=s.root) == s.current


def test_dry_run_no_runtime_current_or_pair_mutation(pinvi):
    s = pinvi
    before = copy.deepcopy(s.records)
    result = apply(s, execute=False)
    assert result["success"] and result["result"] == "planned"
    assert not mutations(s) and s.records == before
    assert release.read_current_release("pinvi", root=s.root) == s.current


def test_existing_cli_accepts_explicit_pinvi_image_without_pair_rebuild():
    args = cli.build_parser().parse_args([
        "service-release", "apply-candidate", "pinvi", "--image-id", paired.NEW,
        "--source-sha", "d" * 40, "--source-clone", "/approved/pinvi", "--dry-run"])
    assert args.release_action == "apply-candidate" and args.service == "pinvi"


@pytest.mark.parametrize("kind", ["missing-receipt", "valid-old-owner", "uncommitted-live"])
def test_companion_keeps_original_pair_owner_while_map_ui_current_advances(pinvi, kind):
    s = pinvi
    record = copy.deepcopy(s.records[MAP_UI])
    record["Id"] = "cid-companion"
    record["Config"]["Labels"]["com.docker.compose.service"] = "map-ui-companion"
    record["Image"] = MAP_CURRENT if kind == "uncommitted-live" else paired.OLD
    s.records["map-ui-companion"] = record
    s.resolved["services"]["map-ui-companion"] = {"image": MAP_CURRENT}
    if kind != "missing-receipt":
        s.status.images["map-ui-companion"] = paired.OLD
    if kind == "valid-old-owner":
        before = copy.deepcopy(record)
        result = apply(s)
        assert result["success"] is True
        assert s.records["map-ui-companion"] == before
        assert json.loads(mutations(s)[0][1]["input"])["services"]["map-ui-companion"]["image"] == paired.OLD
    else:
        with pytest.raises(release.ServiceReleaseError):
            apply(s)
        assert not mutations(s)


def test_initial_pinvi_current_must_match_committed_pair_web(pinvi):
    s = pinvi
    assert s.current["previous"] is None
    s.status.images[WEB] = paired.OLD
    with pytest.raises(release.ServiceReleaseError, match="최초"):
        apply(s)
    assert not mutations(s)


def test_extra_baked_public_input_is_not_silently_adopted(pinvi):
    s = pinvi
    s.metadata[paired.NEW]["Config"]["Env"].append("NEXT_PUBLIC_NEW_INPUT=unapproved")
    with pytest.raises(release.ServiceReleaseError, match="공개 입력"):
        apply(s)
    assert not mutations(s)
