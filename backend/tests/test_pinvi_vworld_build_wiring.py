"""PinVi 공개 지도 키의 원천/빌드 인자/최종 이미지 전달 경계를 고정한다."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from test_f1d_compose_contract import (
    _map_runtime_services,
    _pinvi_runtime_services,
    _resolved_compose,
)

from kor_travel_docker_manager.services.c6c_deployment import (
    C6cBuildProvenance,
    DeploymentContractError,
    validate_c6c_build_source_wiring,
    validate_resolved_c6c_build_provenance,
)

_ROOT = Path(__file__).resolve().parents[2]
_KEY = "NEXT_PUBLIC_VWORLD_API_KEY"


@pytest.mark.parametrize("value", ["", "public-vworld-fixture"])
def test_pinvi_web_build_receives_canonical_vworld_value(value: str) -> None:
    resolved = _resolved_compose(
        *_pinvi_runtime_services(), environment_update={_KEY: value}
    )
    assert resolved["services"]["pinvi-web"]["build"]["args"][_KEY] == value


@pytest.mark.parametrize("wrong_source", [None, "$" + "{PINVI_VWORLD_API_KEY:-}"])
def test_pinvi_raw_build_refuses_missing_or_substitute_vworld_source(
    wrong_source: str | None,
) -> None:
    candidate = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    validate_c6c_build_source_wiring(candidate)
    changed = deepcopy(candidate)
    args = changed["services"]["pinvi-web"]["build"]["args"]
    if wrong_source is None:
        args.pop(_KEY, None)
    else:
        args[_KEY] = wrong_source
    with pytest.raises(DeploymentContractError, match="pinvi-web.*wiring"):
        validate_c6c_build_source_wiring(changed)


def test_pinvi_resolved_build_refuses_missing_vworld_argument() -> None:
    map_revision, pinvi_revision = "a" * 40, "b" * 40
    resolved = _resolved_compose(
        *_map_runtime_services(),
        *_pinvi_runtime_services(),
        environment_update={
            "KOR_TRAVEL_MAP_GIT_COMMIT": map_revision,
            "PINVI_SOURCE_REVISION": pinvi_revision,
            "PINVI_BUILD_ENVIRONMENT": "production",
            _KEY: "public-vworld-fixture",
        },
    )
    provenance = C6cBuildProvenance(
        map_source_revision=map_revision, pinvi_source_revision=pinvi_revision
    )
    validate_resolved_c6c_build_provenance(resolved, provenance)
    del resolved["services"]["pinvi-web"]["build"]["args"][_KEY]
    with pytest.raises(DeploymentContractError, match="pinvi-web.*provenance build args"):
        validate_resolved_c6c_build_provenance(resolved, provenance)
