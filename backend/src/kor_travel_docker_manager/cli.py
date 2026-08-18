import argparse
import json
import sys
import time
from typing import Any

from kor_travel_docker_manager.services.c6c_deployment import (
    DeploymentContractError,
)
from kor_travel_docker_manager.services.c6c_pair_capture import (
    BUILD_FLAG_NOTICE,
    PairCaptureRefusal,
    capture_compatible_pair,
)
from kor_travel_docker_manager.services.compose_service import (
    compose_service,
)
from kor_travel_docker_manager.services.docker_service import docker_service
from kor_travel_docker_manager.services.registry import list_targets
from kor_travel_docker_manager.services.standalone_backup import (
    BACKUP_ROLES,
    StandaloneBackupError,
    create_standalone_backup,
    gc_standalone_backups,
    list_standalone_backups,
)

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
        if not args.confirm:
            print(
                "pinvi-pair rebuild-pinned requires --confirm "
                "(no mutation was attempted)",
                file=sys.stderr,
            )
            return 2
        result = compose_service.rebuild_pinned_runtime()
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _emit_process_result(result, json_output=args.json)


def _cmd_pinvi_pair_capture(args: argparse.Namespace) -> int:
    if args.build:
        print(BUILD_FLAG_NOTICE, file=sys.stderr)
    try:
        result = capture_compatible_pair(
            verified_compatible=args.verified_compatible,
            manifest_path=args.manifest_path,
            map_source_checkout=args.map_source_checkout,
            pinvi_source_checkout=args.pinvi_source_checkout,
            expect_active_map_revision=args.expect_active_map_revision,
            allow_generation_change=args.allow_generation_change,
            build_flag=args.build,
        )
    except PairCaptureRefusal as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode
    return _emit_process_result(result, json_output=args.json)


def _cmd_db_backup_create(args: argparse.Namespace) -> int:
    try:
        manifest = create_standalone_backup(args.role, timeout=args.timeout)
    except StandaloneBackupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(manifest.to_json(), ensure_ascii=False, indent=2))
    else:
        print(
            f"{manifest.role}: {manifest.backup_filename} "
            f"({manifest.byte_size} bytes, {manifest.duration_sec:.1f}s, "
            f"toc={manifest.toc_entry_count}, alembic={manifest.alembic_head or 'unknown'}, "
            f"sha256={manifest.sha256[:12]}...)"
        )
    return 0


def _cmd_db_backup_list(args: argparse.Namespace) -> int:
    try:
        manifests = list_standalone_backups(args.role)
    except StandaloneBackupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([m.to_json() for m in manifests], ensure_ascii=False, indent=2))
        return 0
    if not manifests:
        print(f"no backups for role {args.role}")
        return 0
    for manifest in manifests:
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(manifest.created_at_unix))
        print(
            f"{created_at}  {manifest.role:16s}  {manifest.byte_size:>14d}B  "
            f"alembic={manifest.alembic_head or 'unknown':16s}  "
            f"{manifest.sha256[:12]}…  {manifest.backup_filename}"
        )
    return 0


def _cmd_db_backup_gc(args: argparse.Namespace) -> int:
    try:
        deleted = gc_standalone_backups(args.role, keep=args.keep)
    except StandaloneBackupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"role": args.role, "deleted": deleted}, ensure_ascii=False, indent=2))
    elif deleted:
        print(f"deleted {len(deleted)} backup(s): {', '.join(deleted)}")
    else:
        print("nothing to delete")
    return 0


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
        help="Map·PinVi seven-service runtime을 candidate-first로 재구축합니다.",
    )
    pair_subparsers = pinvi_pair.add_subparsers(dest="pair_action", required=True)
    pair_rebuild = pair_subparsers.add_parser(
        "rebuild-pinned",
        help="고정 release candidate를 검증한 뒤 세 DB를 비우고 일곱 runtime을 재기동합니다.",
    )
    pair_rebuild.add_argument(
        "--confirm",
        action="store_true",
        help="세 Map·Dagster·PinVi DB를 파기형으로 재생성함을 확인합니다.",
    )
    pair_rebuild.add_argument(
        "--json",
        action="store_true",
        help="secret-free 실행 결과 metadata를 JSON으로 출력합니다.",
    )
    pair_rebuild.set_defaults(func=_cmd_pinvi_pair)

    pair_capture = pair_subparsers.add_parser(
        "capture",
        help=(
            "실행 중인 다섯 Map·PinVi 컨테이너를 읽어 C7 runner용 "
            "compatible-pair-v4 manifest를 갱신합니다(컨테이너 불변)."
        ),
    )
    pair_capture.add_argument(
        "--verified-compatible",
        action="store_true",
        help="Map과 PinVi가 같은 contract generation임을 operator가 단언합니다.",
    )
    pair_capture.add_argument(
        "--manifest-path",
        default=None,
        help=(
            "C7 runner가 E2E_C7_COMPATIBLE_PAIR_MANIFEST로 읽는 절대경로 override. "
            "생략하면 frozen 환경의 E2E_C7_COMPATIBLE_PAIR_MANIFEST "
            "(없으면 KTDM_C6C_COMPATIBLE_PAIR_MANIFEST)에서 읽습니다. "
            "basename은 compatible-pair-v4.json이어야 합니다."
        ),
    )
    pair_capture.add_argument(
        "--map-source-checkout",
        default=None,
        help=(
            "관측된 Map revision의 commit object 실재를 확인할 git checkout 절대경로 "
            "override. 생략하면 frozen 환경의 KTDM_C7_MAP_SOURCE_CHECKOUT을 씁니다."
        ),
    )
    pair_capture.add_argument(
        "--pinvi-source-checkout",
        default=None,
        help=(
            "관측된 PinVi revision의 commit object 실재를 확인할 git checkout 절대경로 "
            "override. 생략하면 frozen 환경의 KTDM_C7_PINVI_SOURCE_CHECKOUT을 씁니다."
        ),
    )
    pair_capture.add_argument(
        "--expect-active-map-revision",
        default=None,
        help="주어지면 관측된 Map OCI revision과 exact 일치를 요구합니다(40-hex).",
    )
    pair_capture.add_argument(
        "--allow-generation-change",
        action="store_true",
        help=(
            "기존 manifest의 contract generation이 frozen "
            "KTDM_C6C_CONTRACT_GENERATION과 다를 때에도 진행합니다(기본은 거부)."
        ),
    )
    pair_capture.add_argument(
        "--build",
        action="store_true",
        help="런북 문구 호환용. 수락하지만 아무것도 빌드하지 않습니다.",
    )
    pair_capture.add_argument(
        "--json",
        action="store_true",
        help="secret-free receipt 전체를 JSON으로 출력합니다.",
    )
    pair_capture.set_defaults(func=_cmd_pinvi_pair_capture)

    db_backup = subparsers.add_parser(
        "db-backup",
        help="전용 PostgreSQL 인스턴스(geo/concierge/map/pinvi) 백업을 생성/조회/정리합니다.",
    )
    db_backup_subparsers = db_backup.add_subparsers(dest="db_backup_action", required=True)

    db_backup_create = db_backup_subparsers.add_parser(
        "create", help="지정 role의 pg_dump 백업을 생성합니다."
    )
    db_backup_create.add_argument("role", choices=BACKUP_ROLES)
    db_backup_create.add_argument(
        "--timeout",
        type=int,
        default=14_400,
        help="pg_dump/copy-out 제한 시간(초). geo처럼 큰 인스턴스는 늘려야 합니다.",
    )
    db_backup_create.add_argument("--json", action="store_true")
    db_backup_create.set_defaults(func=_cmd_db_backup_create)

    db_backup_list = db_backup_subparsers.add_parser(
        "list", help="지정 role의 백업 이력을 출력합니다."
    )
    db_backup_list.add_argument("role", choices=BACKUP_ROLES)
    db_backup_list.add_argument("--json", action="store_true")
    db_backup_list.set_defaults(func=_cmd_db_backup_list)

    db_backup_gc = db_backup_subparsers.add_parser(
        "gc", help="오래된 백업을 지우고 최신 --keep개만 보존합니다."
    )
    db_backup_gc.add_argument("role", choices=BACKUP_ROLES)
    db_backup_gc.add_argument(
        "--keep", type=int, required=True, help="보존할 최신 백업 개수(1 이상)."
    )
    db_backup_gc.add_argument("--json", action="store_true")
    db_backup_gc.set_defaults(func=_cmd_db_backup_gc)

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
