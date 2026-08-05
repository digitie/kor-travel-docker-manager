import argparse
import getpass
import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.compose_service import (
    _DEFAULT_C6C_WAIT_TIMEOUT_SECONDS,
    STANDALONE_BACKUP_DEFAULT_KEEP_COUNT,
    STANDALONE_BACKUP_DEFAULT_KEEP_DAYS,
    compose_service,
)
from kor_travel_docker_manager.services.docker_service import docker_service
from kor_travel_docker_manager.services.registry import list_targets

_TRUSTED_ROOT_LAUNCHER_ENV = "KTDM_TRUSTED_ROOT_LAUNCHER"
_TRUSTED_ROOT_LAUNCHER_VALUE = "ktdctl-map-ui-auth-rotate-v1"
_TRUSTED_ROOT_PROJECT_ROOT = "/opt/kor-travel-docker-manager"

DIRECT_ENSURE_ALIASES = {
    alias for target in list_targets() for alias in [target["id"], *target.get("aliases", [])]
}


def _emit_process_result(result: dict[str, Any], *, json_output: bool = False) -> int:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        stdout = result.get("stdout")
        stderr = result.get("stderr")
        if stdout:
            print(stdout, end="" if stdout.endswith("\n") else "\n")
        if stderr:
            print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
    return int(result.get("returncode", 1))


def _cmd_targets(args: argparse.Namespace) -> int:
    targets = list_targets()
    if args.json:
        print(json.dumps(targets, ensure_ascii=False, indent=2))
        return 0

    for target in targets:
        sequence = " -> ".join(target.get("resolved_sequence", []))
        services = ", ".join(target.get("resolved_services", []))
        aliases = ", ".join(target.get("aliases", []))
        alias_text = f" aliases=[{aliases}]" if aliases else ""
        print(
            f"{target['id']}: {target['display_name']} "
            f"sequence=[{sequence}] services=[{services}]{alias_text}"
        )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        result = compose_service.status_target(args.target)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _emit_process_result(result, json_output=args.json)


def _cmd_ensure(args: argparse.Namespace) -> int:
    try:
        result = compose_service.ensure_target(
            args.target,
            build=args.build,
            recreate=args.recreate,
            capture_output=not args.stream,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _emit_process_result(result, json_output=args.json)


def _cmd_logs(args: argparse.Namespace) -> int:
    result = compose_service.logs(
        args.name,
        follow=args.follow,
        tail=args.tail,
        capture_output=not args.follow,
    )
    return _emit_process_result(result, json_output=args.json)


def _cmd_action(args: argparse.Namespace) -> int:
    try:
        result = docker_service.control_container(args.container, args.action)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("success"):
        print(result.get("message", "ok"))
    else:
        print(result.get("error", "unknown error"), file=sys.stderr)
    return 0 if result.get("success") else 1


def _cmd_inspect(args: argparse.Namespace) -> int:
    result = docker_service.inspect_container(args.container)
    if not result.get("success"):
        print(result.get("error", "unknown error"), file=sys.stderr)
        return 1

    container = result["container"]
    if args.json:
        print(json.dumps(container, ensure_ascii=False, indent=2))
        return 0

    print(f"name: {container['name']}")
    print(f"image: {container.get('image')}")
    print(f"status: {container.get('status')}")
    print(f"role: {container.get('role')}")
    print(f"ports: {json.dumps(container.get('network', {}).get('ports', {}), ensure_ascii=False)}")
    print(f"mounts: {len(container.get('mounts', []))}")
    print(f"networks: {', '.join(container.get('network', {}).get('networks', {}).keys())}")
    return 0


def _cmd_pinvi_pair(args: argparse.Namespace) -> int:
    try:
        if args.pair_action == "deploy":
            result = compose_service.deploy_compatible_pinvi_pair(
                build=args.build,
                recreate=True,
                wait_timeout=args.wait_timeout,
                expected_alembic_head=args.expected_alembic_head,
            )
        elif args.pair_action == "capture":
            result = compose_service.capture_compatible_pinvi_pair(
                verified_compatible=args.verified_compatible,
                build=args.build,
                wait_timeout=args.wait_timeout,
            )
        else:
            result = compose_service.rollback_compatible_pinvi_pair()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _emit_process_result(result, json_output=args.json)


def _cmd_cache_target(args: argparse.Namespace) -> int:
    try:
        if args.cache_target_action == "cutover":
            result = compose_service.run_cache_target_cutover(
                cutover_id=args.cutover_id,
                expected_restore_epoch=args.expected_restore_epoch,
                reason=args.reason,
                wait_timeout=args.wait_timeout,
            )
        elif args.cache_target_action == "initial":
            result = compose_service.run_cache_target_initial_cutover(
                cutover_id=args.cutover_id,
                expected_restore_epoch=args.expected_restore_epoch,
                reason=args.reason,
            )
        elif args.cache_target_action == "diagnose":
            result = compose_service.run_cache_target_diagnostic(
                diagnostic_id=args.diagnostic_id,
            )
        elif args.cache_target_action == "bootstrap":
            if not args.confirm:
                print(
                    "cache-target bootstrap requires --confirm (no mutation was attempted)",
                    file=sys.stderr,
                )
                return 2
            result = compose_service.bootstrap_cache_target_default_off()
        else:
            result = compose_service.enable_cache_target_sync()
    except (DeploymentContractError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _emit_process_result(result, json_output=args.json)


def _cmd_db_backup_create(args: argparse.Namespace) -> int:
    try:
        result = compose_service.create_standalone_backup(role=args.role)
    except (DeploymentContractError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _emit_process_result(result, json_output=args.json)


def _cmd_db_backup_restore(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            "db-backup restore requires --confirm (no destructive action was attempted)",
            file=sys.stderr,
        )
        return 2
    try:
        result = compose_service.restore_standalone_backup(
            role=args.role,
            backup_filename=args.backup_id,
            expected_schema_revision=args.expected_schema_revision,
        )
    except (DeploymentContractError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _emit_process_result(result, json_output=args.json)


def _cmd_db_backup_list(args: argparse.Namespace) -> int:
    try:
        result = compose_service.list_standalone_backups(
            role=args.role,
            gc=args.gc,
            keep_count=args.keep_count,
            keep_days=args.keep_days,
        )
    except (DeploymentContractError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not args.json:
        for warning in result["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)
        if args.gc:
            for summary in result["gc"]:
                for deleted in summary["deleted"]:
                    print(
                        f"deleted: {deleted['role']} {deleted['backup_filename']}",
                        file=sys.stderr,
                    )
        for backup in result["backups"]:
            created_at = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(backup["created_at_unix"])
            )
            print(
                f"{created_at}\t{backup['role']}\t{backup['backup_filename']}\t"
                f"schema={backup['schema_revision']}\tsize={backup['byte_size']}\t"
                f"sha256={backup['sha256']}"
            )
        return 0
    return _emit_process_result(result, json_output=True)


def _cmd_map_ui_auth_rotate(args: argparse.Namespace) -> int:
    try:
        _require_trusted_map_ui_auth_launcher()
        _reject_trusted_root_project_override(args.project_root)
        current_password, new_password = _read_map_ui_auth_passwords(
            password_stdin=args.password_stdin,
        )
        result = _load_map_ui_auth_rotator()(
            current_password=current_password,
            new_password=new_password,
            project_root=args.project_root,
        ).as_process_result()
    except (DeploymentContractError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _emit_process_result(result, json_output=args.json)


def _require_trusted_map_ui_auth_launcher() -> None:
    if os.geteuid() != 0:
        return
    if os.environ.get(_TRUSTED_ROOT_LAUNCHER_ENV) != _TRUSTED_ROOT_LAUNCHER_VALUE:
        raise DeploymentContractError(
            "root Map UI auth rotation must be launched through /usr/local/sbin/ktdctl-map-ui-auth-rotate"
        )


def _reject_trusted_root_project_override(project_root: str | None) -> None:
    if os.geteuid() != 0:
        return
    if os.environ.get(_TRUSTED_ROOT_LAUNCHER_ENV) != _TRUSTED_ROOT_LAUNCHER_VALUE:
        return
    if project_root != _TRUSTED_ROOT_PROJECT_ROOT:
        raise DeploymentContractError(
            "trusted root Map UI auth launcher forbids --project-root override"
        )


def _load_map_ui_auth_rotator() -> Callable[..., Any]:
    from kor_travel_docker_manager.services.map_ui_auth_rotation import rotate_map_ui_auth

    return rotate_map_ui_auth


def _read_map_ui_auth_passwords(*, password_stdin: bool) -> tuple[str, str]:
    if password_stdin:
        current_password, new_password = _read_exact_two_stdin_lines()
    else:
        if not sys.stdin.isatty():
            raise ValueError("interactive password input requires a TTY")
        current_password = getpass.getpass("Current Map UI password: ")
        new_password = getpass.getpass("New Map UI password: ")
        confirm_password = getpass.getpass("Confirm new Map UI password: ")
        if new_password != confirm_password:
            raise ValueError("new Map UI password confirmation does not match")
    return current_password, new_password


def _read_exact_two_stdin_lines() -> tuple[str, str]:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    raw = stream.read(8193)
    if isinstance(raw, str):
        raw_bytes = raw.encode("utf-8")
    else:
        raw_bytes = raw
    if len(raw_bytes) > 8192:
        raise ValueError("--password-stdin input is too large")
    if raw_bytes.count(b"\n") != 2 or not raw_bytes.endswith(b"\n"):
        raise ValueError("--password-stdin requires exactly two newline-terminated lines")
    try:
        lines = raw_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("--password-stdin must be UTF-8") from exc
    if len(lines) != 2:
        raise ValueError("--password-stdin requires exactly two lines")
    return lines[0], lines[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ktdctl",
        description="Kor Travel 개발 인프라 Docker 관리 CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    targets = subparsers.add_parser("targets", help="관리 target 목록을 출력합니다.")
    targets.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    targets.set_defaults(func=_cmd_targets)

    status = subparsers.add_parser("status", help="target의 docker compose 상태를 출력합니다.")
    status.add_argument("target", nargs="?", default="all")
    status.add_argument(
        "--json", action="store_true", help="실행 결과 metadata를 JSON으로 출력합니다."
    )
    status.set_defaults(func=_cmd_status)

    ensure = subparsers.add_parser("ensure", help="target 의존 Docker 서비스를 실행합니다.")
    ensure.add_argument("target")
    ensure.add_argument(
        "--build", action="store_true", help="docker compose up에 --build를 전달합니다."
    )
    ensure.add_argument(
        "--recreate", action="store_true", help="docker compose up에 --force-recreate를 전달합니다."
    )
    ensure.add_argument(
        "--stream", action="store_true", help="docker compose 출력을 실시간으로 표시합니다."
    )
    ensure.add_argument(
        "--json", action="store_true", help="실행 결과 metadata를 JSON으로 출력합니다."
    )
    ensure.set_defaults(func=_cmd_ensure)

    logs = subparsers.add_parser("logs", help="target 또는 compose service 로그를 출력합니다.")
    logs.add_argument("name")
    logs.add_argument("--follow", "-f", action="store_true", help="로그를 계속 따라갑니다.")
    logs.add_argument("--tail", type=int, default=100, help="마지막 N줄을 출력합니다.")
    logs.add_argument(
        "--json", action="store_true", help="실행 결과 metadata를 JSON으로 출력합니다."
    )
    logs.set_defaults(func=_cmd_logs)

    action = subparsers.add_parser(
        "action", help="관리 컨테이너에 start/stop/restart를 실행합니다."
    )
    action.add_argument("container")
    action.add_argument("action", choices=["start", "stop", "restart"])
    action.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    action.set_defaults(func=_cmd_action)

    inspect = subparsers.add_parser("inspect", help="관리 컨테이너 상세 정보를 출력합니다.")
    inspect.add_argument("container")
    inspect.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    inspect.set_defaults(func=_cmd_inspect)

    pinvi_pair = subparsers.add_parser(
        "pinvi-pair",
        help="검증된 Map+PinVi immutable image pair를 기록하거나 함께 rollback합니다.",
    )
    pair_subparsers = pinvi_pair.add_subparsers(dest="pair_action", required=True)
    pair_deploy = pair_subparsers.add_parser(
        "deploy",
        help="production Map+PinVi compatible pair를 단계 검증하며 배포합니다.",
    )
    pair_deploy.add_argument("--build", action="store_true", help="이미지를 먼저 빌드합니다.")
    pair_deploy.add_argument(
        "--wait-timeout",
        type=int,
        default=_DEFAULT_C6C_WAIT_TIMEOUT_SECONDS,
        help=(
            f"각 활성화 단계가 healthy를 기다리는 초 단위 상한(기본 "
            f"{_DEFAULT_C6C_WAIT_TIMEOUT_SECONDS}초). "
            "kor-travel-map API는 uvicorn 기동 전에 alembic 마이그레이션을 실행하므로, "
            "긴 마이그레이션을 수반하는 배포는 더 큰 값을 지정해야 timeout으로 인한 "
            "오발동 rollback을 피할 수 있다(issue #88)."
        ),
    )
    pair_deploy.add_argument(
        "--expected-alembic-head",
        default=None,
        help=(
            "candidate Map API 이미지의 alembic head가 이 값과 다르면 배포를 "
            "시작하기 전에 거부합니다(기동/DB 접속 없이 이미지만 정적으로 확인, "
            "issue #109). 생략하면 이 검사를 하지 않습니다 — 알고 있는 배포에서는 "
            "항상 지정해야 합니다."
        ),
    )
    pair_deploy.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    pair_deploy.set_defaults(func=_cmd_pinvi_pair)
    pair_capture = pair_subparsers.add_parser(
        "capture",
        help="clean 환경에서 candidate runtime set을 검증하고 최초 v4를 기록합니다.",
    )
    pair_capture.add_argument(
        "--build", action="store_true", help="candidate runtime 이미지를 먼저 빌드합니다."
    )
    pair_capture.add_argument(
        "--wait-timeout",
        type=int,
        default=_DEFAULT_C6C_WAIT_TIMEOUT_SECONDS,
        help=(
            f"각 부트스트랩 단계가 healthy를 기다리는 초 단위 상한(기본 "
            f"{_DEFAULT_C6C_WAIT_TIMEOUT_SECONDS}초). clean bootstrap은 전체 마이그레이션 "
            "이력을 처음부터 실행할 수 있어 증분 배포보다 오래 걸릴 수 있다(issue #88)."
        ),
    )
    pair_capture.add_argument(
        "--verified-compatible",
        action="store_true",
        help="candidate Map+PinVi image가 같은 contract generation임을 명시합니다.",
    )
    pair_capture.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    pair_capture.set_defaults(func=_cmd_pinvi_pair)
    pair_rollback = pair_subparsers.add_parser(
        "rollback",
        help="manifest의 Map+PinVi image ID를 두 서비스 함께 복원합니다.",
    )
    pair_rollback.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    pair_rollback.set_defaults(func=_cmd_pinvi_pair)

    cache_target = subparsers.add_parser(
        "cache-target",
        help="production cache-target initial cutover와 durable sync enable을 실행합니다.",
    )
    cache_target_subparsers = cache_target.add_subparsers(
        dest="cache_target_action",
        required=True,
    )
    cache_target_cutover = cache_target_subparsers.add_parser(
        "cutover",
        help="H35와 generation 7 cache-target을 하나의 durable window로 전환합니다.",
    )
    cache_target_cutover.add_argument("--cutover-id", required=True)
    cache_target_cutover.add_argument(
        "--expected-restore-epoch",
        required=True,
        type=int,
    )
    cache_target_cutover.add_argument("--reason", required=True)
    cache_target_cutover.add_argument(
        "--wait-timeout",
        type=int,
        default=_DEFAULT_C6C_WAIT_TIMEOUT_SECONDS,
    )
    cache_target_cutover.add_argument(
        "--json",
        action="store_true",
        help="JSON으로 출력합니다.",
    )
    cache_target_cutover.set_defaults(func=_cmd_cache_target)
    cache_target_initial = cache_target_subparsers.add_parser(
        "initial",
        help="sync=false frozen pair에서 idempotent initial cutover runner를 실행합니다.",
    )
    cache_target_initial.add_argument("--cutover-id", required=True)
    cache_target_initial.add_argument(
        "--expected-restore-epoch",
        required=True,
        type=int,
    )
    cache_target_initial.add_argument("--reason", required=True)
    cache_target_initial.add_argument(
        "--json",
        action="store_true",
        help="JSON으로 출력합니다.",
    )
    cache_target_initial.set_defaults(func=_cmd_cache_target)
    cache_target_enable = cache_target_subparsers.add_parser(
        "enable",
        help="durable journal과 causal canary로 sync를 활성화하거나 rollback합니다.",
    )
    cache_target_enable.add_argument(
        "--json",
        action="store_true",
        help="JSON으로 출력합니다.",
    )
    cache_target_enable.set_defaults(func=_cmd_cache_target)
    cache_target_diagnose = cache_target_subparsers.add_parser(
        "diagnose",
        help=(
            "writer fence 안에서 3-role DB 사전 진단(archive/restore rehearsal)을 "
            "실행합니다. cutover를 시작하거나 대체하지 않습니다."
        ),
    )
    cache_target_diagnose.add_argument("--diagnostic-id", required=True)
    cache_target_diagnose.add_argument(
        "--json",
        action="store_true",
        help="JSON으로 출력합니다.",
    )
    cache_target_diagnose.set_defaults(func=_cmd_cache_target)
    cache_target_bootstrap = cache_target_subparsers.add_parser(
        "bootstrap",
        help="완전 미구성 production env에 cache-target default-off 4-role contract를 원자 provision합니다.",
    )
    cache_target_bootstrap.add_argument(
        "--confirm",
        action="store_true",
        help="canonical .env의 secret-free default-off binding 생성에 동의합니다.",
    )
    cache_target_bootstrap.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    cache_target_bootstrap.set_defaults(func=_cmd_cache_target)

    db_backup = subparsers.add_parser(
        "db-backup",
        help="cache-target cutover와 무관하게 언제든 단독으로 DB 백업을 만듭니다.",
    )
    db_backup_subparsers = db_backup.add_subparsers(
        dest="db_backup_action",
        required=True,
    )
    db_backup_create = db_backup_subparsers.add_parser(
        "create",
        help="pg_dump 백업을 만들고 owner-only manifest를 남깁니다. mutation 없음.",
    )
    db_backup_create.add_argument(
        "--role",
        required=True,
        choices=["map_application", "map_dagster", "pinvi"],
    )
    db_backup_create.add_argument(
        "--json",
        action="store_true",
        help="JSON으로 출력합니다.",
    )
    db_backup_create.set_defaults(func=_cmd_db_backup_create)
    db_backup_list = db_backup_subparsers.add_parser(
        "list",
        help="owner-only manifest를 읽어 백업 목록을 보여줍니다. --gc로 보존 정책을 적용합니다.",
    )
    db_backup_list.add_argument(
        "--role",
        choices=["map_application", "map_dagster", "pinvi"],
        default=None,
        help="생략하면 세 role 모두를 보여줍니다.",
    )
    db_backup_list.add_argument(
        "--gc",
        action="store_true",
        help="조회 직후 보존 정책(--keep-count/--keep-days)을 적용해 오래된 백업을 지웁니다.",
    )
    db_backup_list.add_argument(
        "--keep-count",
        type=int,
        default=STANDALONE_BACKUP_DEFAULT_KEEP_COUNT,
        help="나이와 무관하게 항상 보존할 최근 백업 개수(role별). 기본 5.",
    )
    db_backup_list.add_argument(
        "--keep-days",
        type=int,
        default=STANDALONE_BACKUP_DEFAULT_KEEP_DAYS,
        help="keep-count를 넘는 백업 중 보존할 최대 일수. 기본 14.",
    )
    db_backup_list.add_argument(
        "--json",
        action="store_true",
        help="JSON으로 출력합니다.",
    )
    db_backup_list.set_defaults(func=_cmd_db_backup_list)
    db_backup_restore = db_backup_subparsers.add_parser(
        "restore",
        help=(
            "선택한 백업으로 database를 복구합니다. --confirm과 "
            "--expected-schema-revision(현재 상태 명시) 없이는 절대 실행하지 않습니다."
        ),
    )
    db_backup_restore.add_argument(
        "--role",
        required=True,
        choices=["map_application", "map_dagster", "pinvi"],
    )
    db_backup_restore.add_argument(
        "--backup-id",
        required=True,
        help="`db-backup list`가 보여주는 backup_filename(예: ..._map_application_....dump).",
    )
    db_backup_restore.add_argument(
        "--expected-schema-revision",
        required=True,
        help=(
            "복구 대상 database의 현재 schema revision을 명시합니다. 실제 값과 "
            "다르면 어떤 mutation도 없이 즉시 거부합니다."
        ),
    )
    db_backup_restore.add_argument(
        "--confirm",
        action="store_true",
        help="명시하지 않으면 아무 것도 하지 않고 거부합니다(fail-closed 기본값).",
    )
    db_backup_restore.add_argument(
        "--json",
        action="store_true",
        help="JSON으로 출력합니다.",
    )
    db_backup_restore.set_defaults(func=_cmd_db_backup_restore)

    map_ui_auth = subparsers.add_parser(
        "map-ui-auth",
        help="production Map UI 인증 credential을 감사 가능한 workflow로 회전합니다.",
    )
    map_ui_auth_subparsers = map_ui_auth.add_subparsers(
        dest="map_ui_auth_action",
        required=True,
    )
    map_ui_auth_rotate = map_ui_auth_subparsers.add_parser(
        "rotate",
        help="Map UI password hash와 session secret을 함께 회전합니다.",
    )
    map_ui_auth_rotate.add_argument(
        "--password-stdin",
        action="store_true",
        help="현재 password와 새 password를 stdin 두 줄로 입력합니다.",
    )
    map_ui_auth_rotate.add_argument(
        "--project-root",
        help="docker-compose.yml과 .env가 있는 canonical manager checkout 경로입니다.",
    )
    map_ui_auth_rotate.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    map_ui_auth_rotate.set_defaults(func=_cmd_map_ui_auth_rotate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parsed_argv = list(sys.argv[1:] if argv is None else argv)
    if parsed_argv and parsed_argv[0] in DIRECT_ENSURE_ALIASES:
        parsed_argv = ["ensure", parsed_argv[0], *parsed_argv[1:]]
    parser = build_parser()
    args = parser.parse_args(parsed_argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
