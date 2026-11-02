"""`compose-boundary activate-concierge`: canonical Concierge 네 runtime의 검증·재생성.

2026-08-26의 1회성 legacy override 이관(stage/retire)은 2026-11-02에 지웠다. 재생성 경로는
배포 뒤 재생성과 운영 rollback(직전 이미지 재활성화)이 그대로 쓴다.
"""

from __future__ import annotations

import fcntl
import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from kor_travel_docker_manager.services import c6c_deployment as c6c_module
from kor_travel_docker_manager.services import concierge_activation as activation_module
from kor_travel_docker_manager.services.concierge_activation import (
    ComposeConfigResult,
    ConciergeActivationError,
    activate_canonical_concierge,
)

_RECREATED = [
    "kor-travel-concierge-api",
    "kor-travel-concierge-mcp",
    "kor-travel-concierge-dagster-code-server",
    "kor-travel-concierge-ui",
]


def _write(path: Path, content: str, *, mode: int) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def _canonical_compose_document() -> dict[str, object]:
    return {
        "services": {
            "kor-travel-concierge-api": {
                "image": "alpine:3.20",
                "network_mode": c6c_module._CONCIERGE_CANONICAL_RAW_NETWORK_MODE,
                "command": list(c6c_module._CONCIERGE_API_CANONICAL_RAW_COMMAND),
                "environment": dict(c6c_module._CONCIERGE_API_CANONICAL_RAW_ENV_VALUES)
            },
            "kor-travel-concierge-ui": {
                "image": "alpine:3.20",
                "network_mode": c6c_module._CONCIERGE_CANONICAL_RAW_NETWORK_MODE,
                "command": list(c6c_module._CONCIERGE_UI_CANONICAL_RAW_COMMAND),
                "environment": dict(c6c_module._CONCIERGE_UI_CANONICAL_RAW_ENV_VALUES)
            },
            "kor-travel-concierge-mcp": {
                "image": "alpine:3.20",
                "network_mode": c6c_module._CONCIERGE_CANONICAL_RAW_NETWORK_MODE,
            },
            "kor-travel-concierge-dagster-code-server": {
                "image": "alpine:3.20",
                "network_mode": c6c_module._CONCIERGE_CANONICAL_RAW_NETWORK_MODE,
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
    api["network_mode"] = c6c_module._CONCIERGE_CANONICAL_RESOLVED_NETWORK_MODE
    api["command"] = list(c6c_module._CONCIERGE_API_CANONICAL_RESOLVED_COMMAND)
    ui["network_mode"] = c6c_module._CONCIERGE_CANONICAL_RESOLVED_NETWORK_MODE
    ui["command"] = list(
        c6c_module._concierge_ui_expected_command(values, resolved=True)
    )
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


def _must_not_run(*_args: object) -> object:
    pytest.fail("Compose must not run")


def _canonical_tree(tmp_path: Path) -> tuple[Path, Path]:
    """canonical root `.env`(Concierge 값이 이미 root authority에 있는 운영 모양)와 Compose."""

    root = tmp_path / "kor-travel-docker-manager"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    root_env = root / ".env"
    _write(
        root_env,
        "KOR_TRAVEL_CONCIERGE_REPO_DIR=../kor-travel-concierge\n"
        "KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY='concierge-bff-key'\n"
        "KOR_TRAVEL_CONCIERGE_API_KEYS='concierge-old-key,concierge-bff-key'\n"
        "KOR_TRAVEL_CONCIERGE_APP_ENV='production'\n"
        "KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED='true'\n"
        "KOR_TRAVEL_CONCIERGE_UI_ADMIN_USERNAME='admin'\n"
        "KOR_TRAVEL_CONCIERGE_UI_ADMIN_PASSWORD_HASH='pbkdf2_sha256$100000$testhash$quotetest'\n"
        "KOR_TRAVEL_CONCIERGE_UI_SESSION_SECRET='session-$#value-012345678901234567890123'\n"
        "KOR_TRAVEL_CONCIERGE_UI_ADMIN_PROXY_SECRET='proxy-$#value-01234567890123456789012345'\n"
        "KOR_TRAVEL_CONCIERGE_UI_TRUST_FORWARDED_IPS='false'\n"
        "KOR_TRAVEL_CONCIERGE_UI_PUBLIC_ORIGINS='https://concierge.example.test'\n"
        "KOR_TRAVEL_CONCIERGE_UI_VWORLD_SERVICE_KEY='vworld-$#legacy-key'\n"
        "KOR_TRAVEL_CONCIERGE_UI_PUBLIC_API_BASE_URL=''\n",
        mode=0o600,
    )
    _write(
        root / "docker-compose.yml",
        yaml.safe_dump(_canonical_compose_document(), sort_keys=False),
        mode=0o644,
    )
    return root, root_env


def test_activate_canonical_concierge_recreates_only_exact_service_set(tmp_path: Path) -> None:
    root, root_env = _canonical_tree(tmp_path)
    observed: list[tuple[list[str], Path]] = []

    def config_runner(
        command: list[str], project_root: Path, values: dict[str, str]
    ) -> ComposeConfigResult:
        observed.append((command, project_root))
        return _canonical_config_result(values)

    def up_runner(command: list[str], project_root: Path, _values: dict[str, str]) -> int:
        observed.append((command, project_root))
        return 0

    activate_canonical_concierge(
        project_root=root,
        compose_config_runner=config_runner,
        compose_up_runner=up_runner,
        require_root=False,
    )

    assert [command[command.index("--file") + 2] for command, _ in observed] == ["config", "up"]
    up_command = observed[-1][0]
    assert up_command[-4:] == _RECREATED
    assert "--no-deps" in up_command
    assert "--force-recreate" in up_command
    # 이미지가 없을 때 운영 source 디렉터리에서 implicit build하지 않는다.
    assert "--no-build" in up_command
    # Compose는 trusted root의 `.env`와 cwd만 쓴다.
    assert all(project_root == root for _command, project_root in observed)
    assert all(str(root_env) in command for command, _project_root in observed)
    # projection 임시 파일은 실행 뒤 남지 않는다.
    assert not list(root.glob(".concierge-c6c-*"))


def test_activation_projects_out_unrelated_required_candidate_values(tmp_path: Path) -> None:
    root, _root_env = _canonical_tree(tmp_path)
    compose_path = root / "docker-compose.yml"
    document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    document["services"]["pinvi-db-runtime-role"] = {
        "environment": {
            "PINVI_APP_DB_PASSWORD": "${PINVI_APP_DB_PASSWORD:?must be set}",
        }
    }
    document["services"]["kor-travel-concierge-api"]["depends_on"] = {
        "concierge-support": {"condition": "service_started"},
    }
    document["services"]["concierge-support"] = {"image": "alpine:3.20"}
    document["services"]["kor-travel-concierge-api"]["secrets"] = ["concierge-secret"]
    document["secrets"] = {
        "concierge-secret": {"environment": "CONCIERGE_SECRET"},
        "pinvi-secret": {"environment": "PINVI_SECRET"},
    }
    compose_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    compose_path.chmod(0o644)
    observed_projection_documents: list[dict[str, object]] = []

    def observe(command: list[str]) -> None:
        projection_path = Path(command[command.index("--file") + 1])
        projection = yaml.safe_load(projection_path.read_text(encoding="utf-8"))
        assert isinstance(projection, dict)
        observed_projection_documents.append(projection)

    def config_runner(
        command: list[str], _project_root: Path, values: dict[str, str]
    ) -> ComposeConfigResult:
        observe(command)
        return _canonical_config_result(values)

    def up_runner(command: list[str], _project_root: Path, _values: dict[str, str]) -> int:
        observe(command)
        return 0

    activate_canonical_concierge(
        project_root=root,
        compose_config_runner=config_runner,
        compose_up_runner=up_runner,
        require_root=False,
    )

    # 검증과 재생성은 같은 projection을 쓴다.
    assert len(observed_projection_documents) == 2
    assert observed_projection_documents[0] == observed_projection_documents[1]
    for projected in observed_projection_documents:
        services = projected["services"]
        assert isinstance(services, dict)
        assert "pinvi-db-runtime-role" not in services
        assert "concierge-support" in services
        assert projected["secrets"] == {"concierge-secret": {"environment": "CONCIERGE_SECRET"}}


def test_activation_refuses_when_config_fails_without_recreate(tmp_path: Path) -> None:
    root, _root_env = _canonical_tree(tmp_path)

    with pytest.raises(ConciergeActivationError, match="canonical Compose validation failed"):
        activate_canonical_concierge(
            project_root=root,
            compose_config_runner=lambda *_args: ComposeConfigResult(1, ""),
            compose_up_runner=_must_not_run,  # type: ignore[arg-type]
            require_root=False,
        )


def test_activation_refuses_resolved_c6c_contract_drift_without_recreate(tmp_path: Path) -> None:
    root, _root_env = _canonical_tree(tmp_path)

    def mismatched_config(
        _command: list[str], _project_root: Path, values: dict[str, str]
    ) -> ComposeConfigResult:
        resolved = json.loads(_canonical_config_result(values).stdout)
        resolved["services"]["kor-travel-concierge-ui"]["environment"][
            "BACKEND_ORIGIN"
        ] = "http://unsafe.example.test"
        return ComposeConfigResult(returncode=0, stdout=json.dumps(resolved))

    with pytest.raises(ConciergeActivationError, match="C6c contract"):
        activate_canonical_concierge(
            project_root=root,
            compose_config_runner=mismatched_config,
            compose_up_runner=_must_not_run,  # type: ignore[arg-type]
            require_root=False,
        )


def test_activation_refuses_raw_ui_production_command_drift(tmp_path: Path) -> None:
    root, _root_env = _canonical_tree(tmp_path)
    compose_path = root / "docker-compose.yml"
    compose_path.write_text(
        compose_path.read_text(encoding="utf-8").replace(
            "npm run build && exec npm run start", "npm run dev"
        ),
        encoding="utf-8",
    )
    compose_path.chmod(0o644)

    with pytest.raises(ConciergeActivationError, match="C6c contract"):
        activate_canonical_concierge(
            project_root=root,
            compose_config_runner=_valid_config_runner,
            compose_up_runner=_must_not_run,  # type: ignore[arg-type]
            require_root=False,
        )


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("network_mode", "bridge"),
        ("api_port", "12602"),
    ],
)
def test_activation_refuses_resolved_runtime_boundary_drift(
    tmp_path: Path, target: str, value: str
) -> None:
    root, _root_env = _canonical_tree(tmp_path)

    def mismatched_config(
        _command: list[str], _project_root: Path, values: dict[str, str]
    ) -> ComposeConfigResult:
        resolved = json.loads(_canonical_config_result(values).stdout)
        services = resolved["services"]
        if target == "network_mode":
            services["kor-travel-concierge-ui"]["network_mode"] = value
        else:
            services["kor-travel-concierge-api"]["command"][-1] = value
        return ComposeConfigResult(returncode=0, stdout=json.dumps(resolved))

    with pytest.raises(ConciergeActivationError, match="C6c contract"):
        activate_canonical_concierge(
            project_root=root,
            compose_config_runner=mismatched_config,
            compose_up_runner=_must_not_run,  # type: ignore[arg-type]
            require_root=False,
        )


def test_activation_refuses_api_auth_downgrade_in_root_environment(tmp_path: Path) -> None:
    root, root_env = _canonical_tree(tmp_path)
    _write(
        root_env,
        root_env.read_text(encoding="utf-8").replace(
            "KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED='true'",
            "KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED='false'",
        ),
        mode=0o600,
    )

    with pytest.raises(ConciergeActivationError, match="C6c contract"):
        activate_canonical_concierge(
            project_root=root,
            compose_config_runner=_valid_config_runner,
            compose_up_runner=_must_not_run,  # type: ignore[arg-type]
            require_root=False,
        )


def test_activation_reports_recreate_failure(tmp_path: Path) -> None:
    root, _root_env = _canonical_tree(tmp_path)

    with pytest.raises(ConciergeActivationError, match="recreation failed"):
        activate_canonical_concierge(
            project_root=root,
            compose_config_runner=_valid_config_runner,
            compose_up_runner=lambda *_args: 1,
            require_root=False,
        )

    assert not list(root.glob(".concierge-c6c-*"))


@pytest.mark.parametrize("mode", [0o640, 0o644])
def test_activation_refuses_a_readable_root_environment(tmp_path: Path, mode: int) -> None:
    root, root_env = _canonical_tree(tmp_path)
    root_env.chmod(mode)

    with pytest.raises(ConciergeActivationError, match="unsafe ownership or mode"):
        activate_canonical_concierge(
            project_root=root,
            compose_config_runner=_must_not_run,  # type: ignore[arg-type]
            compose_up_runner=_must_not_run,  # type: ignore[arg-type]
            require_root=False,
        )


def test_activation_refuses_a_held_mutation_lock_without_compose(tmp_path: Path) -> None:
    root, _root_env = _canonical_tree(tmp_path)
    lock_path = root / "activation.lock"
    lock_path.touch(mode=0o600)
    lock_path.chmod(0o600)
    with lock_path.open("r+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ConciergeActivationError, match="mutation lock"):
            activate_canonical_concierge(
                project_root=root,
                compose_config_runner=_must_not_run,  # type: ignore[arg-type]
                compose_up_runner=_must_not_run,  # type: ignore[arg-type]
                lock_path=str(lock_path),
                require_root=False,
            )


def test_root_execution_rejects_callers_project_root_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(activation_module.os, "geteuid", lambda: 0)

    with pytest.raises(ConciergeActivationError, match="project root is fixed"):
        activation_module._prepare_project_context(project_root=tmp_path, require_root=True)


def test_non_root_execution_is_refused_in_production_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(activation_module.os, "geteuid", lambda: 1000)

    with pytest.raises(ConciergeActivationError, match="requires root execution"):
        activate_canonical_concierge()


def test_root_rehearsal_boundary_uses_the_global_mutation_lock() -> None:
    """ADR-51 C-2: activate는 host 변경 lock G를 잡는다.

    C-3부터 Manager의 파일 lock은 G 하나라 rebuild·pin 회전·M05·installer와 모두 이것으로
    직렬화된다. conftest가 G를 테스트마다 tmp로 옮겨 두므로 기대값은 그 seam 경로다.
    """

    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
    }

    selected = activation_module._select_lock_path(
        values,
        project_root=Path("/irrelevant"),
        lock_path=None,
        require_root=True,
    )

    assert selected == str(c6c_module._C6C_GLOBAL_MUTATION_LOCK)


@pytest.mark.parametrize(
    "values",
    [
        {"KTDM_DEPLOYMENT_ENVIRONMENT": "local"},
        {"KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal"},
        {
            "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
            "KTDM_DEPLOYMENT_LIFECYCLE": "operational",
            "PINVI_ENVIRONMENT": "production",
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        },
    ],
)
def test_root_boundary_rejects_noncanonical_execution_environment(
    values: dict[str, str],
) -> None:
    with pytest.raises(ConciergeActivationError, match="requires"):
        activation_module._select_lock_path(
            values,
            project_root=Path("/irrelevant"),
            lock_path=None,
            require_root=True,
        )


def test_root_execution_refuses_a_caller_lock_path() -> None:
    with pytest.raises(ConciergeActivationError, match="lock path is fixed"):
        activation_module._select_lock_path(
            {"KTDM_DEPLOYMENT_ENVIRONMENT": "production"},
            project_root=Path("/irrelevant"),
            lock_path="/tmp/elsewhere.lock",
            require_root=True,
        )


def test_project_context_follows_the_release_symlink_without_resolving_it(
    tmp_path: Path,
) -> None:
    """설치 root는 현재 release를 가리키는 symlink다(ADR-51 D).

    검사는 따라가서 하되 돌려주는 root는 풀지 않는다 — Compose cwd·`--file`·projection
    임시 파일이 모두 이 경로를 쓰므로 풀린 release 경로가 들어가면 상대 bind가 거기
    묶인다.
    """

    release = tmp_path / "ktdm-release-a"
    release.mkdir(mode=0o755)
    release.chmod(0o755)
    (release / ".env").write_text("COMPOSE_PROJECT_NAME=ktdm\n", encoding="utf-8")
    (release / ".env").chmod(0o600)
    (release / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (release / "docker-compose.yml").chmod(0o644)
    link = tmp_path / "kor-travel-docker-manager"
    link.symlink_to(release.name, target_is_directory=True)

    context = activation_module._prepare_project_context(
        project_root=link, require_root=False
    )

    assert context.root == link
    assert context.compose_path == link / "docker-compose.yml"
    assert context.env_path == link / ".env"
