"""전용 Feature read credential의 파생·단일 소비·마스킹 경계를 검증한다."""
import hashlib
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from test_c6c_cancel_probe_resume import _rehearsal_environment
from test_f1d_compose_contract import (
    _compose_contract_environment,
    _map_runtime_services,
    _pinvi_runtime_services,
    _resolved_compose,
    _valid_runtime_configs,
)

from kor_travel_docker_manager.services import c6c_deployment as c6c
from kor_travel_docker_manager.services.docker_service import (
    ContainerConfigValidationError,
    _redact_env_pair,
    validate_container_config_update,
)
from kor_travel_docker_manager.services.secret_scrub import redact_structure

_RAW = "PINVI_KOR_TRAVEL_MAP_ADMIN_FEATURES_READ_TOKEN"
_DIGEST = "KOR_TRAVEL_MAP_API_PINVI_ADMIN_FEATURES_READ_TOKEN_SHA256"
_TOKEN = "pinvi-admin-feature-read-fixture-token-0000"
_SHA = hashlib.sha256(_TOKEN.encode()).hexdigest()
_ROOT = Path(__file__).resolve().parents[2]


def _environment():
    return {**_compose_contract_environment(), _RAW: _TOKEN}


def test_raw_only_derives_digest_without_mutating_or_persisting_inputs():
    before = _environment()
    result = c6c.derive_curation_service_principal_environment(before)
    assert _DIGEST not in before
    assert result[_DIGEST] == _SHA and result[_RAW] == _TOKEN
    assert result["KOR_TRAVEL_MAP_API_SERVICE_TOKEN"] == before["KOR_TRAVEL_MAP_API_SERVICE_TOKEN"]


def test_unconfigured_read_principal_stays_disabled():
    result = c6c.derive_curation_service_principal_environment(_compose_contract_environment())
    assert result[_DIGEST] == ""
    assert not result.get(_RAW)


@pytest.mark.parametrize("updates", [
    {_DIGEST: _SHA},
    {_RAW: _TOKEN, _DIGEST: "0" * 64},
    {_RAW: "short"},
    {_RAW: _TOKEN + "\n"},
    {_RAW: "\0" + _TOKEN},
])
def test_invalid_or_digest_only_input_is_fail_closed(updates):
    environment = _compose_contract_environment()
    environment.update(updates)
    with pytest.raises(c6c.DeploymentContractError):
        c6c.derive_curation_service_principal_environment(environment)


@pytest.mark.parametrize("other_name", [
    "KOR_TRAVEL_MAP_API_SERVICE_TOKEN",
    "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET",
    "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN",
    "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN",
    "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN",
    "PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN",
    "PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN",
    "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN",
])
def test_existing_credential_cannot_be_promoted_to_feature_read(other_name):
    environment = _compose_contract_environment()
    environment[_RAW] = environment[other_name]
    with pytest.raises(c6c.DeploymentContractError, match="must differ"):
        c6c.derive_curation_service_principal_environment(environment)


def test_two_canonical_api_holders_only_and_no_bootstrap_frontend_or_dagster():
    services = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())["services"]
    holders = {(name, key) for name, item in services.items()
               for key in (item.get("environment") or {}) if key in {_RAW, _DIGEST}}
    assert holders == {("kor-travel-map-api", _DIGEST), ("pinvi-api", _RAW)}
    for service, key in holders:
        assert services[service]["environment"][key] == "$" + "{" + key + ":-}"


def test_frozen_compose_derives_map_digest_and_keeps_pinvi_raw():
    resolved = _resolved_compose(
        *_map_runtime_services(), *_pinvi_runtime_services(),
        environment_update={_RAW: _TOKEN},
    )
    assert resolved["services"]["kor-travel-map-api"]["environment"][_DIGEST] == _SHA
    assert resolved["services"]["pinvi-api"]["environment"][_RAW] == _TOKEN


@pytest.mark.parametrize("service,key", [("kor-travel-map-api", _DIGEST), ("pinvi-api", _RAW)])
def test_contract_editor_cannot_replace_delete_or_add_protected_source(service, key):
    baseline = {key: "$" + "{" + key + ":-}"}
    assert key in c6c.contract_locked_env_names(service)
    with pytest.raises(ContainerConfigValidationError, match="리터럴"):
        validate_container_config_update(
            ports=[], env={key: "literal-override"}, networks=[],
            baseline_env=baseline, service_name=service,
        )
    for before, after in [(baseline, {key: "$" + "{UNRELATED:-}"}), (baseline, {}), ({}, baseline)]:
        with pytest.raises(ContainerConfigValidationError, match="배포 계약이 고정"):
            validate_container_config_update(
                ports=[], env=after, networks=[], baseline_env=before, service_name=service
            )


def test_inspection_and_nested_logs_mask_raw_and_digest():
    assert _TOKEN not in _redact_env_pair(_RAW + "=" + _TOKEN)
    assert _SHA not in _redact_env_pair(_DIGEST + "=" + _SHA)
    redacted = redact_structure({"nested": [_TOKEN, _SHA]}, {_RAW: _TOKEN, _DIGEST: _SHA})
    assert _TOKEN not in str(redacted) and _SHA not in str(redacted)


def _config():
    return c6c.load_c6c_deployment_config_from_environment({
        **_rehearsal_environment(), _RAW: _TOKEN,
    })


def test_config_holds_read_token_without_repr_disclosure():
    config = _config()
    assert config.admin_features_read_token == _TOKEN
    assert _TOKEN not in repr(config)


def test_runtime_isolation_requires_both_enabled_exact_destinations():
    config = _config()
    runtime = _valid_runtime_configs(config)
    runtime[config.map_container]["Env"].append(_DIGEST + "=" + _SHA)
    runtime[config.pinvi_container]["Env"].append(_RAW + "=" + _TOKEN)
    c6c.validate_runtime_secret_isolation(runtime, config)
    for holder, key in [(config.map_container, _DIGEST), (config.pinvi_container, _RAW)]:
        missing = deepcopy(runtime)
        missing[holder]["Env"] = [p for p in missing[holder]["Env"] if not p.startswith(key + "=")]
        with pytest.raises(c6c.DeploymentContractError):
            c6c.validate_runtime_secret_isolation(missing, config)
    for path in ["Env", "Cmd", "Labels"]:
        leaked = deepcopy(runtime)
        leaked[config.map_ui_container][path] = (
            ["UNRELATED=" + _TOKEN] if path == "Env" else [_TOKEN] if path == "Cmd" else {"x": _SHA}
        )
        with pytest.raises(c6c.DeploymentContractError):
            c6c.validate_runtime_secret_isolation(leaked, config)


def test_disabled_runtime_does_not_require_new_fields_but_rejects_nonempty_read_token():
    config = replace(_config(), admin_features_read_token="")
    runtime = _valid_runtime_configs(config)
    c6c.validate_runtime_secret_isolation(runtime, config)
    runtime[config.pinvi_container]["Env"].append(_RAW + "=" + _TOKEN)
    with pytest.raises(c6c.DeploymentContractError):
        c6c.validate_runtime_secret_isolation(runtime, config)
