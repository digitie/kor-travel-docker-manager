from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_contract import CacheTargetRuntimeContract
from kor_travel_docker_manager.services.cache_target_production_manifest import (
    CACHE_TARGET_PRODUCTION_PINS,
    CacheTargetProductionPinManifest,
    require_cache_target_production_release,
)
from kor_travel_docker_manager.services.compose_service import (
    _require_cache_target_release,
)

_ROOT = Path(__file__).resolve().parents[2]


def _contract() -> CacheTargetRuntimeContract:
    return CacheTargetRuntimeContract(
        registry_json="[]",
        sync_enabled="false",
        command_token="c" * 32,
        consumer_token="u" * 32,
        restore_fence_token="r" * 32,
        recovery_token="v" * 32,
        consumer_id="pinvi-cache-target-consumer",
        expected_openapi_sha256=CACHE_TARGET_PRODUCTION_PINS.service_openapi_sha256,
        expected_source_revision=CACHE_TARGET_PRODUCTION_PINS.map_release_revision,
        expected_contract_generation=CACHE_TARGET_PRODUCTION_PINS.contract_generation,
        role_binding_sha256="b" * 64,
    )


def test_tracked_release_is_the_current_generation_seven_pair() -> None:
    assert CACHE_TARGET_PRODUCTION_PINS == CacheTargetProductionPinManifest(
        version=2,
        map_release_revision="1df45b57f55b8d517bb1f2c12a869d032d70453e",
        pinvi_release_revision="2d598551287d84c3af13510f8cab7f8bec547715",
        service_openapi_sha256=(
            "6ad8c1c9c1d391c54e7592b64ed9f0225164b613a5c2824d8eafd3da9bd36f1e"
        ),
        contract_generation="7",
        map_application_alembic_head="0084_c6c_cancel_probe_fixtures",
    )
    assert tuple(CACHE_TARGET_PRODUCTION_PINS.__dataclass_fields__) == (
        "version",
        "map_release_revision",
        "pinvi_release_revision",
        "service_openapi_sha256",
        "contract_generation",
        "map_application_alembic_head",
    )


def test_pinset_sha256_uses_only_compact_sorted_v2_semantic_fields() -> None:
    canonical_json = (
        '{"contract_generation":"7","map_application_alembic_head":'
        '"0084_c6c_cancel_probe_fixtures","map_release_revision":'
        '"1df45b57f55b8d517bb1f2c12a869d032d70453e","pinvi_release_revision":'
        '"2d598551287d84c3af13510f8cab7f8bec547715","service_openapi_sha256":'
        '"6ad8c1c9c1d391c54e7592b64ed9f0225164b613a5c2824d8eafd3da9bd36f1e",'
        '"version":2}'
    )

    assert CACHE_TARGET_PRODUCTION_PINS.canonical_pinset_json() == canonical_json
    assert (
        CACHE_TARGET_PRODUCTION_PINS.pinset_sha256
        == "0144b4c4b6b31b39f2bb32002d8990777e8834e8a5d14cce9b3fee56aa5d0b27"
    )


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"version": 1}, "version"),
        ({"version": True}, "version"),
        ({"map_release_revision": "A" * 40}, "map_release_revision"),
        ({"pinvi_release_revision": "x" * 40}, "pinvi_release_revision"),
        ({"service_openapi_sha256": "0" * 63}, "service_openapi_sha256"),
        ({"contract_generation": 7}, "contract_generation"),
        ({"contract_generation": "07"}, "contract_generation"),
        ({"map_application_alembic_head": "0083-bad"}, "map_application_alembic_head"),
    ],
)
def test_pin_manifest_rejects_noncanonical_semantic_fields(
    changes: dict[str, object], field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        replace(CACHE_TARGET_PRODUCTION_PINS, **changes)


def test_map_migration_head_is_required_compose_input_with_v2_example_default() -> None:
    compose = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    example = (_ROOT / ".env.example").read_text(encoding="utf-8")

    assert (
        "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD: "
        "${KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD:?KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD "
        "must be explicitly set}"
    ) in compose
    assert "0078_cache_target_gc_observe" not in compose
    assert (
        "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD=0084_c6c_cancel_probe_fixtures"
    ) in example


def test_unconfigured_cache_target_does_not_require_release_pin() -> None:
    _require_cache_target_release(SimpleNamespace(cache_target=None))  # type: ignore[arg-type]


def test_release_requires_exact_contract_candidate_and_both_pairs() -> None:
    release = CACHE_TARGET_PRODUCTION_PINS.pinvi_release_revision
    pairs = (
        SimpleNamespace(
            map_source_revision=CACHE_TARGET_PRODUCTION_PINS.map_release_revision,
            pinvi_source_revision=release,
        ),
        SimpleNamespace(
            map_source_revision=CACHE_TARGET_PRODUCTION_PINS.map_release_revision,
            pinvi_source_revision=release,
        ),
    )

    require_cache_target_production_release(
        _contract(),
        pairs=pairs,
        candidate_map_source_revision=(CACHE_TARGET_PRODUCTION_PINS.map_release_revision),
        candidate_source_revision=release,
    )

    with pytest.raises(DeploymentContractError, match="tracked pin manifest"):
        require_cache_target_production_release(
            replace(_contract(), expected_contract_generation="6"),
        )
    with pytest.raises(DeploymentContractError, match="tracked pin manifest"):
        require_cache_target_production_release(
            replace(
                _contract(),
                expected_source_revision="e12494bd5c4b5b2e1d51c72b6ddcf18eead0e53f",
            ),
        )
    with pytest.raises(DeploymentContractError, match="candidate differs"):
        require_cache_target_production_release(
            _contract(),
            candidate_source_revision="b" * 40,
        )
    with pytest.raises(DeploymentContractError, match="candidate differs"):
        require_cache_target_production_release(
            _contract(),
            candidate_source_revision="a" * 40,
        )
    with pytest.raises(DeploymentContractError, match="Map candidate differs"):
        require_cache_target_production_release(
            _contract(),
            candidate_map_source_revision="b" * 40,
        )
    with pytest.raises(DeploymentContractError, match="compatible pair differs"):
        require_cache_target_production_release(
            _contract(),
            pairs=(
                pairs[0],
                SimpleNamespace(
                    map_source_revision=(CACHE_TARGET_PRODUCTION_PINS.map_release_revision),
                    pinvi_source_revision="b" * 40,
                ),
            ),
        )
    with pytest.raises(DeploymentContractError, match="compatible pair differs"):
        require_cache_target_production_release(
            _contract(),
            pairs=(
                pairs[0],
                SimpleNamespace(
                    map_source_revision="c" * 40,
                    pinvi_source_revision=release,
                ),
            ),
        )
