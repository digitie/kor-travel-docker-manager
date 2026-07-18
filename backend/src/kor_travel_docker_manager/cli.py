import argparse
import json
import sys
from typing import Any

from kor_travel_docker_manager.services.compose_service import compose_service
from kor_travel_docker_manager.services.docker_service import docker_service
from kor_travel_docker_manager.services.registry import list_targets

DIRECT_ENSURE_ALIASES = {
    alias
    for target in list_targets()
    for alias in [target["id"], *target.get("aliases", [])]
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
            )
        elif args.pair_action == "capture":
            result = compose_service.capture_compatible_pinvi_pair(
                verified_compatible=args.verified_compatible,
                build=args.build,
            )
        else:
            result = compose_service.rollback_compatible_pinvi_pair()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _emit_process_result(result, json_output=args.json)


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
    pair_deploy.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    pair_deploy.set_defaults(func=_cmd_pinvi_pair)
    pair_capture = pair_subparsers.add_parser(
        "capture",
        help="clean/legacy 환경에서 candidate pair를 검증하고 최초 v2를 기록합니다.",
    )
    pair_capture.add_argument(
        "--build", action="store_true", help="두 API candidate 이미지를 먼저 빌드합니다."
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
