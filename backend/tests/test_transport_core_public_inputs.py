"""A-SDK-CORE-P2-01: runtime override가 baked client drift를 숨길 수 없다."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
import test_transport_core_candidate as fixtures
from test_transport_core_candidate import NEW_FRONTEND, apply_core

from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services.service_release import ServiceReleaseError

scenario = fixtures.scenario
core_case = fixtures.core_case

PUBLIC = {"NEXT_PUBLIC_API_BASE_URL": "https://old-api.invalid", "NEXT_PUBLIC_API_PORT": "8000"}
OTHER = {"NEXT_PUBLIC_API_BASE_URL": "https://other-api.invalid", "NEXT_PUBLIC_API_PORT": "8123"}
LABEL = "io.kor-travel-transport.public-input-sha256"


def replace_env(entries, key, value):
    rows = [row for row in entries if not row.startswith(key + "=")]
    if value is not None:
        rows.append(key + "=" + value)
    entries[:] = rows


def bind_candidate(s, values):
    for key, value in values.items():
        replace_env(s.metadata[NEW_FRONTEND]["Config"]["Env"], key, value)
    hashes = {key: hashlib.sha256(value.encode()).hexdigest() for key, value in values.items()}
    s.normal["artifacts"]["frontend"]["publicInputHashes"] = hashes
    s.metadata[NEW_FRONTEND]["Config"]["Labels"][LABEL] = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.mark.parametrize("key", PUBLIC)
@pytest.mark.parametrize("execute", [False, True])
def test_matching_baked_inputs_preserve_all_runtime_configuration(core_case, key, execute):
    s = core_case
    before = list(s.records["frontend"]["Config"]["Env"])
    result = apply_core(s, execute=execute)
    assert result["success"] is True
    actual = s.records["frontend"]["Config"]["Env"]
    for row in before:
        if not row.startswith("RELEASE_SHA="):
            assert row in actual
    assert key + "=" + PUBLIC[key] in actual


@pytest.mark.parametrize("key", PUBLIC)
@pytest.mark.parametrize("execute", [False, True])
def test_consistent_candidate_receipt_and_image_but_baked_drift_rejected(core_case, key, execute):
    s = core_case
    candidate = {**PUBLIC, key: OTHER[key]}
    bind_candidate(s, candidate)
    before = copy.deepcopy(s.records)
    with pytest.raises(ServiceReleaseError):
        apply_core(s, execute=execute)
    assert s.records == before and s.steps == ["factory"] and not s.calls
    assert release.read_current_release("transport", root=s.root) == s.current


@pytest.mark.parametrize("key", PUBLIC)
def test_existing_runtime_override_does_not_redefine_approved_baked_baseline(core_case, key):
    s = core_case
    replace_env(s.records["frontend"]["Config"]["Env"], key, OTHER[key])
    result = apply_core(s)
    assert result["success"] is True
    assert key + "=" + OTHER[key] in s.records["frontend"]["Config"]["Env"]
    assert key + "=" + PUBLIC[key] in s.metadata[NEW_FRONTEND]["Config"]["Env"]


@pytest.mark.parametrize("key", PUBLIC)
def test_runtime_only_override_cannot_approve_candidate_baked_drift(core_case, key):
    s = core_case
    replace_env(s.records["frontend"]["Config"]["Env"], key, OTHER[key])
    bind_candidate(s, {**PUBLIC, key: OTHER[key]})
    with pytest.raises(ServiceReleaseError):
        apply_core(s)
    assert s.steps == ["factory"] and not s.calls
    assert release.read_current_release("transport", root=s.root) == s.current


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_hash",
        "extra_hash",
        "bad_label",
        "missing_label",
        "image_default_only",
        "receipt_only",
        "old_default_missing",
    ],
)
def test_incomplete_or_inconsistent_public_proof_refused(core_case, mutation):
    s = core_case
    hashes = s.normal["artifacts"]["frontend"]["publicInputHashes"]
    cfg = s.metadata[NEW_FRONTEND]["Config"]
    if mutation == "missing_hash":
        hashes.pop("NEXT_PUBLIC_API_BASE_URL")
    elif mutation == "extra_hash":
        hashes["NEXT_PUBLIC_UNAPPROVED"] = "a" * 64
    elif mutation == "bad_label":
        cfg["Labels"][LABEL] = "0" * 64
    elif mutation == "missing_label":
        del cfg["Labels"][LABEL]
    elif mutation == "image_default_only":
        replace_env(cfg["Env"], "NEXT_PUBLIC_API_PORT", OTHER["NEXT_PUBLIC_API_PORT"])
    elif mutation == "receipt_only":
        hashes["NEXT_PUBLIC_API_PORT"] = hashlib.sha256(
            OTHER["NEXT_PUBLIC_API_PORT"].encode()
        ).hexdigest()
    elif mutation == "old_default_missing":
        old = s.metadata[s.records["frontend"]["Image"]]["Config"]["Env"]
        replace_env(old, "NEXT_PUBLIC_API_PORT", None)
    with pytest.raises(ServiceReleaseError):
        apply_core(s)
    assert not s.calls and "snapshot" not in s.steps


@pytest.mark.parametrize("key", PUBLIC)
def test_public_input_recheck_before_publication_keeps_old_current(core_case, key):
    s = core_case

    def change():
        replace_env(s.metadata[NEW_FRONTEND]["Config"]["Env"], key, OTHER[key])

    s.hooks["start"] = change
    result = apply_core(s)
    assert result["success"] is False
    assert release.read_current_release("transport", root=s.root) == s.current


def test_empty_base_url_is_an_explicit_baked_baseline(core_case):
    s = core_case
    old = s.metadata[s.records["frontend"]["Image"]]["Config"]["Env"]
    replace_env(old, "NEXT_PUBLIC_API_BASE_URL", "")
    replace_env(s.records["frontend"]["Config"]["Env"], "NEXT_PUBLIC_API_BASE_URL", "")
    bind_candidate(s, {**PUBLIC, "NEXT_PUBLIC_API_BASE_URL": ""})
    assert apply_core(s)["success"] is True
