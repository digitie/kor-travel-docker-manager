from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values

from kor_travel_docker_manager.services import c6c_deployment as c6c_module
from kor_travel_docker_manager.services import legacy_override_retirement as retirement_module
from kor_travel_docker_manager.services.legacy_override_retirement import (
    ComposeConfigResult,
    LegacyOverrideActivationError,
    LegacyOverrideArchiveDurabilityError,
    LegacyOverrideRetirementError,
    LegacyRootEnvironmentDurabilityError,
    activate_canonical_concierge,
    retire_legacy_compose_override,
)


def _write(path: Path, content: str, *, mode: int) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def _canonical_compose_document() -> dict[str, object]:
    return {
        "services": {
            "kor-travel-concierge-api": {
                "image": "alpine:3.20",
                "environment": dict(c6c_module._CONCIERGE_API_CANONICAL_RAW_ENV_VALUES)
            },
            "kor-travel-concierge-ui": {
                "image": "alpine:3.20",
                "environment": dict(c6c_module._CONCIERGE_UI_CANONICAL_RAW_ENV_VALUES)
            },
        }
    }


def _canonical_config_result(values: dict[str, str]) -> ComposeConfigResult:
    resolved = deepcopy(_canonical_compose_document())
    services = resolved["services"]
    assert isinstance(services, dict)
    api = services["kor-travel-concierge-api"]
    ui = services["kor-travel-concierge-ui"]
    assert isinstance(api, dict) and isinstance(ui, dict)
    api_environment = api["environment"]
    ui_environment = ui["environment"]
    assert isinstance(api_environment, dict) and isinstance(ui_environment, dict)
    api_sources = {
        c6c_module._CONCIERGE_UI_ADMIN_PROXY_SECRET_ENV: (
            c6c_module._CONCIERGE_ROOT_PROXY_SECRET_ENV
        ),
        "APP_ENV": c6c_module._CONCIERGE_ROOT_APP_ENV,
        "API_AUTH_ENABLED": c6c_module._CONCIERGE_ROOT_API_AUTH_ENABLED_ENV,
        "API_KEYS": c6c_module._CONCIERGE_ROOT_API_KEYS_ENV,
    }
    for target, source in api_sources.items():
        api_environment[target] = values[source].replace("$", "$$")
    ui_environment[c6c_module._CONCIERGE_UI_BACKEND_ORIGIN_ENV] = (
        c6c_module._CONCIERGE_FIXED_BACKEND_ORIGIN
    )
    for target, source in c6c_module._CONCIERGE_UI_ENV_SOURCES.items():
        default = "false" if target == c6c_module._CONCIERGE_UI_TRUST_FORWARDED_IPS_ENV else ""
        ui_environment[target] = values.get(source, default).replace("$", "$$")
    return ComposeConfigResult(returncode=0, stdout=json.dumps(resolved))


def _valid_config_runner(
    _command: list[str], _project_root: Path, values: dict[str, str]
) -> ComposeConfigResult:
    return _canonical_config_result(values)


def _no_op_up_runner(_command: list[str], _project_root: Path, _values: dict[str, str]) -> int:
    return 0


def _migration_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "kor-travel-docker-manager"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    concierge_root = tmp_path / "kor-travel-concierge"
    concierge_root.mkdir(mode=0o755)
    concierge_root.chmod(0o755)

    root_env = root / ".env"
    _write(
        root_env,
        "KOR_TRAVEL_CONCIERGE_REPO_DIR=../kor-travel-concierge\n"
        "KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY='concierge-bff-key'\n"
        "NEXT_PUBLIC_VWORLD_API_KEY='vworld-$#legacy-key'\n"
        "KOR_TRAVEL_GEO_BACKUP_SCHEDULE_ENABLED=\n"
        "KOR_TRAVEL_GEO_BACKUP_SCHEDULE_INTERVAL_HOURS=\n"
        "KOR_TRAVEL_GEO_BACKUP_ARTIFACT_TTL_DAYS=\n"
        "KOR_TRAVEL_GEO_BACKUP_RETENTION_KEEP_MIN=\n",
        mode=0o600,
    )
    _write(
        root / "docker-compose.yml",
        yaml.safe_dump(_canonical_compose_document(), sort_keys=False),
        mode=0o644,
    )
    override = root / "docker-compose.override.yml"
    override.write_text(
        yaml.safe_dump(
            {
                "services": {
                    service: {
                        "environment": {
                            "KTG_BACKUP_SCHEDULE_ENABLED": "true",
                            "KTG_BACKUP_SCHEDULE_INTERVAL_HOURS": "24",
                            "KTG_BACKUP_ARTIFACT_TTL_DAYS": "7",
                            "KTG_BACKUP_RETENTION_KEEP_MIN": "3",
                        }
                    }
                    for service in (
                        "kor-travel-geo-api",
                        "kor-travel-geo-dagster",
                        "kor-travel-geo-dagster-daemon",
                    )
                }
                | {
                    "kor-travel-concierge-ui": {
                        "command": ["npm", "run", "start"],
                        "env_file": ["../kor-travel-concierge/.env"],
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    override.chmod(0o600)
    source_env = concierge_root / ".env"
    _write(
        source_env,
        "API_KEYS='concierge-old-key,concierge-bff-key'\n"
        "APP_ENV=production\n"
        "API_AUTH_ENABLED=true\n"
        "KTC_ADMIN_USERNAME=admin\n"
        "KTC_ADMIN_PASSWORD_HASH='pbkdf2_sha256$100000$testhash$quotetest'\n"
        "KTC_UI_SESSION_SECRET='session-$#value-012345678901234567890123'\n"
        "KTC_ADMIN_PROXY_SECRET='proxy-$#value-01234567890123456789012345'\n"
        "KTC_UI_TRUST_FORWARDED_IPS=false\n"
        "KTC_UI_PUBLIC_ORIGINS=https://concierge.example.test\n",
        mode=0o600,
    )
    return root, root_env, override


def test_retire_legacy_override_migrates_exact_values_and_archives_after_config(
    tmp_path: Path,
) -> None:
    root, root_env, override = _migration_tree(tmp_path)
    observed: dict[str, object] = {}

    def config_runner(
        command: list[str], project_root: Path, values: dict[str, str]
    ) -> ComposeConfigResult:
        observed.setdefault("commands", []).append(command)
        observed["project_root"] = project_root
        observed["values"] = values
        return _canonical_config_result(values)

    archive = retire_legacy_compose_override(
        project_root=root,
        compose_config_runner=config_runner,
        compose_up_runner=_no_op_up_runner,
        require_root=False,
    )

    assert not override.exists()
    assert archive.parent == root / ".retired-compose-overrides"
    assert archive.exists()
    assert archive.stat().st_mode & 0o777 == 0o600
    assert archive.parent.stat().st_mode & 0o777 == 0o700
    assert observed["commands"] == [[
        "docker",
        "compose",
        "--env-file",
        str(root_env),
        "--file",
        str(root / "docker-compose.yml"),
        "config",
        "--format",
        "json",
    ]] * 2
    assert observed["project_root"] == root
    values = dotenv_values(root_env, interpolate=False)
    assert values["KOR_TRAVEL_GEO_BACKUP_SCHEDULE_ENABLED"] == "true"
    assert values["KOR_TRAVEL_GEO_BACKUP_SCHEDULE_INTERVAL_HOURS"] == "24"
    assert values["KOR_TRAVEL_GEO_BACKUP_ARTIFACT_TTL_DAYS"] == "7"
    assert values["KOR_TRAVEL_GEO_BACKUP_RETENTION_KEEP_MIN"] == "3"
    assert values["KOR_TRAVEL_CONCIERGE_API_KEYS"] == "concierge-old-key,concierge-bff-key"
    assert values["KOR_TRAVEL_CONCIERGE_APP_ENV"] == "production"
    assert values["KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED"] == "true"
    assert values["KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY"] == "concierge-bff-key"
    assert values["KOR_TRAVEL_CONCIERGE_UI_ADMIN_PASSWORD_HASH"] == (
        "pbkdf2_sha256$100000$testhash$quotetest"
    )
    assert values["KOR_TRAVEL_CONCIERGE_UI_SESSION_SECRET"] == (
        "session-$#value-012345678901234567890123"
    )
    assert values["KOR_TRAVEL_CONCIERGE_UI_ADMIN_PROXY_SECRET"] == (
        "proxy-$#value-01234567890123456789012345"
    )
    assert values["KOR_TRAVEL_CONCIERGE_UI_VWORLD_SERVICE_KEY"] == "vworld-$#legacy-key"
    assert values["KOR_TRAVEL_CONCIERGE_UI_PUBLIC_API_BASE_URL"] == ""


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose가 필요함")
def test_retired_root_env_round_trips_special_values_through_compose_config(
    tmp_path: Path,
) -> None:
    root, root_env, _override = _migration_tree(tmp_path)
    retire_legacy_compose_override(
        project_root=root,
        compose_up_runner=_no_op_up_runner,
        require_root=False,
    )

    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(root_env),
            "--file",
            str(root / "docker-compose.yml"),
            "config",
            "--format",
            "json",
        ],
        cwd=root,
        env={**os.environ, "COMPOSE_FILE": ""},
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    resolved = json.loads(completed.stdout)
    environment = resolved["services"]["kor-travel-concierge-ui"]["environment"]
    assert environment["KTC_ADMIN_PASSWORD_HASH"] == "pbkdf2_sha256$$100000$$testhash$$quotetest"
    assert environment["KTC_UI_SESSION_SECRET"] == "session-$$#value-012345678901234567890123"
    assert environment["KTC_ADMIN_PROXY_SECRET"] == "proxy-$$#value-01234567890123456789012345"


def test_retire_legacy_override_rejects_backend_key_not_in_source_set(
    tmp_path: Path,
) -> None:
    root, root_env, override = _migration_tree(tmp_path)
    original_root = root_env.read_bytes()
    original_override = override.read_bytes()

    root_env.write_text(
        root_env.read_text(encoding="utf-8").replace(
            "KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY='concierge-bff-key'",
            "KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY='not-in-source-key-set'",
        ),
        encoding="utf-8",
    )
    root_env.chmod(0o600)

    with pytest.raises(LegacyOverrideRetirementError, match="exact member"):
        retire_legacy_compose_override(
            project_root=root,
            compose_config_runner=lambda *_args: pytest.fail("config must not run"),
            require_root=False,
        )

    assert root_env.read_bytes() != original_root
    assert override.read_bytes() == original_override


def test_retire_legacy_override_rejects_api_auth_downgrade_from_source(
    tmp_path: Path,
) -> None:
    root, root_env, override = _migration_tree(tmp_path)
    source_env = tmp_path / "kor-travel-concierge" / ".env"
    original_root = root_env.read_bytes()
    source_env.write_text(
        source_env.read_text(encoding="utf-8").replace(
            "API_AUTH_ENABLED=true", "API_AUTH_ENABLED=false"
        ),
        encoding="utf-8",
    )
    source_env.chmod(0o600)

    with pytest.raises(LegacyOverrideRetirementError, match="production authentication enabled"):
        retire_legacy_compose_override(
            project_root=root,
            compose_config_runner=lambda *_args: pytest.fail("config must not run"),
            require_root=False,
        )

    assert root_env.read_bytes() == original_root
    assert override.exists()


def test_retire_legacy_override_restores_root_environment_when_config_fails(
    tmp_path: Path,
) -> None:
    root, root_env, override = _migration_tree(tmp_path)
    original_root = root_env.read_bytes()

    with pytest.raises(LegacyOverrideRetirementError, match="canonical Compose validation failed"):
        retire_legacy_compose_override(
            project_root=root,
            compose_config_runner=lambda *_args: ComposeConfigResult(1, ""),
            require_root=False,
        )

    assert root_env.read_bytes() == original_root
    assert override.exists()


def test_retire_legacy_override_restores_root_when_actual_c6c_contract_mismatches(
    tmp_path: Path,
) -> None:
    root, root_env, override = _migration_tree(tmp_path)
    original_root = root_env.read_bytes()

    def mismatched_config(
        _command: list[str], _project_root: Path, values: dict[str, str]
    ) -> ComposeConfigResult:
        resolved = json.loads(_canonical_config_result(values).stdout)
        resolved["services"]["kor-travel-concierge-ui"]["environment"][
            "BACKEND_ORIGIN"
        ] = "http://unsafe.example.test"
        return ComposeConfigResult(returncode=0, stdout=json.dumps(resolved))

    with pytest.raises(LegacyOverrideRetirementError, match="C6c contract"):
        retire_legacy_compose_override(
            project_root=root,
            compose_config_runner=mismatched_config,
            require_root=False,
        )

    assert root_env.read_bytes() == original_root
    assert override.exists()


def test_retire_legacy_override_keeps_candidate_environment_after_archive_durability_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, root_env, override = _migration_tree(tmp_path)

    def fail_archive_directory_sync(path: Path) -> None:
        if path.name == ".retired-compose-overrides":
            raise LegacyOverrideRetirementError("injected archive directory sync failure")

    monkeypatch.setattr(retirement_module, "_fsync_directory", fail_archive_directory_sync)

    with pytest.raises(LegacyOverrideArchiveDurabilityError, match="durability is uncertain"):
        retire_legacy_compose_override(
            project_root=root,
            compose_config_runner=_valid_config_runner,
            require_root=False,
        )

    assert not override.exists()
    assert list((root / ".retired-compose-overrides").iterdir())
    values = dotenv_values(root_env, interpolate=False)
    assert values["KOR_TRAVEL_CONCIERGE_API_KEYS"] == "concierge-old-key,concierge-bff-key"
    assert values["KOR_TRAVEL_GEO_BACKUP_SCHEDULE_ENABLED"] == "true"


def test_retire_legacy_override_reports_candidate_root_durability_uncertainty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, root_env, override = _migration_tree(tmp_path)

    def fail_root_directory_sync(path: Path) -> None:
        if path == root:
            raise LegacyOverrideRetirementError("injected root directory sync failure")

    monkeypatch.setattr(retirement_module, "_fsync_directory", fail_root_directory_sync)

    with pytest.raises(LegacyRootEnvironmentDurabilityError, match="durability is uncertain"):
        retire_legacy_compose_override(
            project_root=root,
            compose_config_runner=_valid_config_runner,
            require_root=False,
        )

    assert override.exists()
    assert dotenv_values(root_env, interpolate=False)["KOR_TRAVEL_CONCIERGE_API_KEYS"]


@pytest.mark.parametrize("mode", [0o640, 0o644])
def test_retire_legacy_override_rejects_readable_concierge_source_environment(
    tmp_path: Path, mode: int
) -> None:
    root, root_env, override = _migration_tree(tmp_path)
    source_env = tmp_path / "kor-travel-concierge" / ".env"
    source_env.chmod(mode)
    original_root = root_env.read_bytes()

    with pytest.raises(LegacyOverrideRetirementError, match="unsafe ownership or mode"):
        retire_legacy_compose_override(
            project_root=root,
            compose_config_runner=lambda *_args: pytest.fail("config must not run"),
            require_root=False,
        )

    assert root_env.read_bytes() == original_root
    assert override.exists()


def test_retire_legacy_override_rejects_malformed_concierge_password_hash(
    tmp_path: Path,
) -> None:
    root, root_env, override = _migration_tree(tmp_path)
    source_env = tmp_path / "kor-travel-concierge" / ".env"
    source_env.write_text(
        source_env.read_text(encoding="utf-8").replace(
            "pbkdf2_sha256$100000$testhash$quotetest", "not-a-password-hash"
        ),
        encoding="utf-8",
    )
    source_env.chmod(0o600)

    with pytest.raises(LegacyOverrideRetirementError, match="authentication values are invalid"):
        retire_legacy_compose_override(
            project_root=root,
            compose_config_runner=lambda *_args: pytest.fail("config must not run"),
            require_root=False,
        )

    assert root_env.read_text(encoding="utf-8").find("KOR_TRAVEL_CONCIERGE_API_KEYS") == -1
    assert override.exists()


@pytest.mark.parametrize(
    "declaration",
    [
        "KTC_ADMIN_USERNAME = duplicate",
        " export KTC_ADMIN_USERNAME=duplicate",
        "KTC_ADMIN_USERNAME\t=duplicate",
        "export\tKTC_ADMIN_USERNAME=duplicate",
    ],
)
def test_retire_legacy_override_rejects_dotenv_duplicate_whitespace_variants(
    tmp_path: Path, declaration: str
) -> None:
    root, root_env, override = _migration_tree(tmp_path)
    source_env = tmp_path / "kor-travel-concierge" / ".env"
    source_env.write_text(
        source_env.read_text(encoding="utf-8") + declaration + "\n",
        encoding="utf-8",
    )
    source_env.chmod(0o600)
    original_root = root_env.read_bytes()

    with pytest.raises(LegacyOverrideRetirementError, match="duplicate migration variables"):
        retire_legacy_compose_override(
            project_root=root,
            compose_config_runner=lambda *_args: pytest.fail("config must not run"),
            require_root=False,
        )

    assert root_env.read_bytes() == original_root
    assert override.exists()


def test_retire_legacy_override_rejects_held_mutation_lock_without_mutation(
    tmp_path: Path,
) -> None:
    root, root_env, override = _migration_tree(tmp_path)
    original_root = root_env.read_bytes()
    lock_path = root / "retirement.lock"
    lock_path.touch(mode=0o600)
    lock_path.chmod(0o600)
    with lock_path.open("r+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(LegacyOverrideRetirementError, match="mutation lock"):
            retire_legacy_compose_override(
                project_root=root,
                compose_config_runner=lambda *_args: pytest.fail("config must not run"),
                lock_path=str(lock_path),
                require_root=False,
            )

    assert root_env.read_bytes() == original_root
    assert override.exists()


def test_activate_canonical_concierge_recreates_only_exact_service_set(
    tmp_path: Path,
) -> None:
    root, _root_env, _override = _migration_tree(tmp_path)
    retire_legacy_compose_override(
        project_root=root,
        compose_config_runner=_valid_config_runner,
        compose_up_runner=_no_op_up_runner,
        require_root=False,
    )
    observed: list[str] = []

    def up_runner(command: list[str], _project_root: Path, _values: dict[str, str]) -> int:
        observed.extend(command)
        return 0

    activate_canonical_concierge(
        project_root=root,
        compose_config_runner=_valid_config_runner,
        compose_up_runner=up_runner,
        require_root=False,
    )

    assert observed[-4:] == [
        "kor-travel-concierge-api",
        "kor-travel-concierge-mcp",
        "kor-travel-concierge-scheduler",
        "kor-travel-concierge-ui",
    ]
    assert "--no-deps" in observed
    assert "--force-recreate" in observed


def test_retire_legacy_override_leaves_canonical_archive_when_recreate_fails(
    tmp_path: Path,
) -> None:
    root, root_env, override = _migration_tree(tmp_path)

    with pytest.raises(LegacyOverrideActivationError, match="was retired"):
        retire_legacy_compose_override(
            project_root=root,
            compose_config_runner=_valid_config_runner,
            compose_up_runner=lambda *_args: 1,
            require_root=False,
        )

    assert not override.exists()
    assert list((root / ".retired-compose-overrides").iterdir())
    assert dotenv_values(root_env, interpolate=False)["KOR_TRAVEL_CONCIERGE_API_KEYS"]


def test_retire_legacy_override_rejects_unrecognized_override_without_mutation(
    tmp_path: Path,
) -> None:
    root, root_env, override = _migration_tree(tmp_path)
    original_root = root_env.read_bytes()
    override.write_text(
        override.read_text(encoding="utf-8") + "networks:\n  unsafe: {}\n",
        encoding="utf-8",
    )
    override.chmod(0o600)

    with pytest.raises(LegacyOverrideRetirementError, match="unsupported top-level"):
        retire_legacy_compose_override(
            project_root=root,
            compose_config_runner=lambda *_args: pytest.fail("config must not run"),
            require_root=False,
        )

    assert root_env.read_bytes() == original_root
    assert override.exists()
