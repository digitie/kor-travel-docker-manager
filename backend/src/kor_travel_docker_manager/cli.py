import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from kor_travel_docker_manager.services.c6c_deployment import (
    DeploymentContractError,
)
from kor_travel_docker_manager.services.compose_service import (
    PinnedRuntimePrejournalFailure,
    compose_service,
)
from kor_travel_docker_manager.services.docker_service import docker_service
from kor_travel_docker_manager.services.legacy_override_retirement import (
    LegacyOverrideRetirementError,
    activate_canonical_concierge,
    retire_legacy_compose_override,
    stage_legacy_compose_override,
)
from kor_travel_docker_manager.services.registry import list_targets
from kor_travel_docker_manager.services.runtime_pin_registry import (
    block_runtime_pinset,
    build_registry,
    load_runtime_pin_registry,
    packaged_seed_path,
    rollback_runtime_pin,
    rotate_runtime_pin,
    runtime_pin_registry_path,
    verify_runtime_pin_registry,
    write_runtime_pin_registry,
)
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
    except PinnedRuntimePrejournalFailure as exc:
        payload = {
            "status": "failed",
            "classification": "prejournal_failure",
            "stage": exc.stage,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                "pinned runtime candidate preparation failed: " + exc.stage,
                file=sys.stderr,
            )
        return 2
    except DeploymentContractError as exc:
        if args.json:
            print(
                json.dumps(
                    {"status": "failed", "classification": "unclassified"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _emit_process_result(result, json_output=args.json)


def _cmd_retire_legacy_override(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            "compose-boundary retire-legacy-override requires --confirm "
            "(no mutation was attempted)",
            file=sys.stderr,
        )
        return 2
    try:
        retire_legacy_compose_override()
    except LegacyOverrideRetirementError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("legacy Compose override retired and canonical Concierge recreated")
    return 0


def _cmd_stage_legacy_override(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            "compose-boundary stage-legacy-override requires --confirm "
            "(no mutation was attempted)",
            file=sys.stderr,
        )
        return 2
    try:
        stage_legacy_compose_override(source_path=Path(args.source))
    except LegacyOverrideRetirementError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("legacy Compose override snapshot staged in the protected runtime boundary")
    return 0


def _cmd_activate_canonical_concierge(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            "compose-boundary activate-concierge requires --confirm "
            "(no mutation was attempted)",
            file=sys.stderr,
        )
        return 2
    try:
        activate_canonical_concierge()
    except LegacyOverrideRetirementError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("canonical Concierge recreated from the single-file boundary")
    return 0


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
        outcome = gc_standalone_backups(args.role, keep=args.keep)
    except StandaloneBackupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {"role": args.role, **outcome.to_json()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if outcome.deleted:
        print(f"deleted {len(outcome.deleted)} backup(s): {', '.join(outcome.deleted)}")
    # 회전과 잔해 수거를 합쳐 세면 "왜 예상보다 많이 지워졌나"를 알 수 없다.
    if outcome.orphans_removed:
        print(
            f"removed {len(outcome.orphans_removed)} orphaned dump(s) left by an interrupted "
            f"backup: {', '.join(outcome.orphans_removed)}"
        )
    if outcome.total == 0:
        print("nothing to delete")
    return 0


def _pin_actor() -> str:
    """회전 주체를 감사 기록에 남긴다."""

    import getpass

    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - 사용자 정보가 없는 환경
        return f"uid:{os.getuid()}" if hasattr(os, "getuid") else "unknown"


def _print_registry(registry: Any, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(registry.to_payload(), ensure_ascii=False, indent=2))
        return
    print(f"pinset   {registry.pinset_sha256}")
    print(f"map      {registry.map_revision}")
    print(f"pinvi    {registry.pinvi_revision}")
    print(f"rotated  {registry.rotated_at} by {registry.rotated_by}")
    print(f"reason   {registry.reason}")
    if registry.is_unconditionally_blocked_pinset(registry.pinset_sha256):
        print()
        print("⚠ 현재 고정된 pinset은 재시도가 금지된 candidate입니다 — 이 상태로는")
        print("  'ktdctl pinvi-pair rebuild-pinned'가 거부됩니다. 새 revision으로 회전하세요:")
        print("  ktdctl pin rotate --role <map|pinvi> --revision <40-hex> \\")
        print('    --reason "<사유>" --confirm')
        print()
    blocked = registry.effective_blocked_pinsets
    if blocked:
        print(f"blocked  {len(blocked)} pinset(s):")
        for entry in blocked:
            scope = f" phase={entry.phase}" if entry.phase else ""
            marker = " <- CURRENT" if entry.pinset_sha256 == registry.pinset_sha256 else ""
            print(f"  - {entry.pinset_sha256}{scope}{marker}")
            print(f"    {entry.reason}")
    if registry.history:
        print(f"history  {len(registry.history)} rotation(s), latest first:")
        for entry in reversed(registry.history[-5:]):
            print(f"  - {entry.rotated_at} {entry.pinset_sha256[:12]}... {entry.reason}")


def _cmd_pin_init(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("pin init requires --confirm (no file was written)", file=sys.stderr)
        return 2
    path = runtime_pin_registry_path()
    if path.exists() and not args.force:
        print(
            f"runtime pin registry already exists at {path.name}; refusing to overwrite "
            "(use --force only to reseed a host)",
            file=sys.stderr,
        )
        return 2
    existing = None
    if path.exists():
        try:
            existing = load_runtime_pin_registry(path=path)
        except DeploymentContractError:
            existing = None
        if existing is not None and existing.history:
            print(
                f"기존 registry의 회전 이력 {len(existing.history)}건을 승계합니다 "
                "(이전 상태는 digest 이름으로 보존됩니다).",
            )
    try:
        seed = load_runtime_pin_registry(path=Path(args.seed))
        registry = build_registry(
            release_version=seed.release_version,
            map_revision=seed.map_revision,
            pinvi_revision=seed.pinvi_revision,
            rotated_by=_pin_actor(),
            reason=args.reason,
            # 재시딩이 이력과 차단 목록을 지우면 롤백 소스와 terminal 규율이 함께
            # 사라진다. 기존 값이 있으면 승계하고, 이전 상태도 보존한다.
            history=existing.history if existing is not None else (),
            # declared 목록만 승계한다. 코드 하한선은 파일이 아니라 코드가 소유하므로
            # 파일에 적어 넣으면 사람이 지울 수 있는 값이 되어 하한선이 아니게 된다.
            blocked_pinsets=(
                existing.blocked_pinsets if existing is not None else seed.blocked_pinsets
            ),
        )
        write_runtime_pin_registry(
            registry, path=path, preserve_previous=existing is not None
        )
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"runtime pin registry bootstrapped at {path.name}")
    _print_registry(registry, json_output=args.json)
    return 0


def _cmd_source_status(args: argparse.Namespace) -> int:
    """배포 provenance를 사람 말로 출력한다. 관측만 하므로 --confirm이 없다."""

    from kor_travel_docker_manager.services.source_status import collect_source_status

    payload = collect_source_status(force_refresh=args.refresh)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        summary = payload["summary"]
        print(f"요약: {summary['text']}")
        if summary.get("next_action"):
            print(f"다음: {summary['next_action']}")
        print()

        def line(label: str, row: dict[str, Any]) -> None:
            print(f"  {label:22s} {row.get('human', {}).get('text', '')}")

        line("설치 기록", payload["manager"])
        for row in payload["checkouts"]:
            line(f"작업 사본 {row['label']}", row)
        for row in payload["running_images"]:
            line(f"실행 중 {row['label']}", row)
        for row in payload["contracts"]:
            line(row["title"], row)
        line(payload["environment"]["title"], payload["environment"])
    # 관측 결과가 '조치 필요'면 비정상 종료로 알린다 — 스크립트에서 게이트로 쓸 수 있다.
    return 1 if payload["summary"]["level"] == "action_required" else 0


def _cmd_pin_show(args: argparse.Namespace) -> int:
    try:
        registry = load_runtime_pin_registry()
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_registry(registry, json_output=args.json)
    return 0


def _cmd_pin_verify(args: argparse.Namespace) -> int:
    try:
        report = verify_runtime_pin_registry()
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for key, value in report.items():
            print(f"{key:24s} {value}")
    exit_code = 0
    if report.get("current_pinset_is_blocked"):
        print(
            "현재 고정된 pinset은 재시도 금지 상태입니다 — rebuild-pinned가 거부됩니다. "
            "'ktdctl pin rotate'로 새 revision을 고정하세요.",
            file=sys.stderr,
        )
        exit_code = 1
    if report.get("published_copy") != "current":
        # 사본 부재는 stale보다 나쁘다 — 조회 API가 영구적으로 unknown/degraded가 된다.
        print(
            f"published copy is {report.get('published_copy')}; the read-only API cannot "
            "report the authoritative pin until a root pin command refreshes it",
            file=sys.stderr,
        )
        exit_code = 1
    return exit_code


def _cmd_pin_rotate(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("pin rotate requires --confirm (no file was written)", file=sys.stderr)
        return 2
    try:
        registry = rotate_runtime_pin(
            role=args.role,
            revision=args.revision,
            reason=args.reason,
            rotated_by=_pin_actor(),
            block_previous=args.block_previous,
        )
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"rotated {args.role} pin; new pinset {registry.pinset_sha256}")
    _print_registry(registry, json_output=args.json)
    return 0


def _cmd_pin_block(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("pin block requires --confirm (no file was written)", file=sys.stderr)
        return 2
    try:
        registry = block_runtime_pinset(
            pinset_sha256=args.pinset,
            reason=args.reason,
            map_revision=args.map_revision,
            pinvi_revision=args.pinvi_revision,
            phase=args.phase,
        )
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"pinset {args.pinset} is now permanently blocked")
    _print_registry(registry, json_output=args.json)
    return 0


def _cmd_pin_rollback(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("pin rollback requires --confirm (no file was written)", file=sys.stderr)
        return 2
    try:
        registry = rollback_runtime_pin(
            pinset_sha256=args.to,
            rotated_by=_pin_actor(),
            reason=args.reason,
        )
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"rolled back to pinset {registry.pinset_sha256}")
    _print_registry(registry, json_output=args.json)
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

    compose_boundary = subparsers.add_parser(
        "compose-boundary",
        help="single-file Compose 경계의 제한된 운영 이관을 실행합니다.",
    )
    compose_boundary_subparsers = compose_boundary.add_subparsers(
        dest="compose_boundary_action", required=True
    )
    stage_legacy_override = compose_boundary_subparsers.add_parser(
        "stage-legacy-override",
        help="legacy override와 고정된 Concierge source를 보호된 state로 snapshot합니다.",
    )
    stage_legacy_override.add_argument(
        "--source",
        required=True,
        help="legacy docker-compose.override.yml의 절대 경로입니다.",
    )
    stage_legacy_override.add_argument(
        "--confirm",
        action="store_true",
        help="owner-only protected stage로의 단방향 snapshot을 확인합니다.",
    )
    stage_legacy_override.set_defaults(func=_cmd_stage_legacy_override)

    retire_legacy_override = compose_boundary_subparsers.add_parser(
        "retire-legacy-override",
        help="보호된 staged override를 canonical root .env로 이관하고 보관합니다.",
    )
    retire_legacy_override.add_argument(
        "--confirm",
        action="store_true",
        help="root .env 갱신과 staged override의 owner-only archive 이동을 확인합니다.",
    )
    retire_legacy_override.set_defaults(func=_cmd_retire_legacy_override)

    activate_concierge = compose_boundary_subparsers.add_parser(
        "activate-concierge",
        help="archive 완료 뒤 canonical Concierge만 검증·재생성합니다.",
    )
    activate_concierge.add_argument(
        "--confirm",
        action="store_true",
        help="API/MCP/scheduler/UI를 canonical single-file source로 재생성함을 확인합니다.",
    )
    activate_concierge.set_defaults(func=_cmd_activate_canonical_concierge)

    source_status = subparsers.add_parser(
        "source-status",
        help="배포 provenance(설치 기록·작업 사본·실행 중 이미지·계약)를 조회합니다.",
    )
    source_status.add_argument(
        "--refresh", action="store_true", help="TTL 캐시를 무시하고 다시 관측합니다."
    )
    source_status.add_argument("--json", action="store_true")
    source_status.set_defaults(func=_cmd_source_status)

    pin = subparsers.add_parser(
        "pin",
        help="Map·PinVi pinned revision registry를 조회/검증/회전합니다.",
    )
    pin_subparsers = pin.add_subparsers(dest="pin_action", required=True)

    pin_init = pin_subparsers.add_parser(
        "init", help="호스트의 runtime pin registry를 최초 1회 생성합니다."
    )
    pin_init.add_argument(
        "--seed",
        default=str(packaged_seed_path()),
        help="부트스트랩 원본 seed 경로. 기본값은 설치본의 config/runtime-pins.seed.json입니다.",
    )
    pin_init.add_argument("--reason", default="host bootstrap", help="생성 사유입니다.")
    pin_init.add_argument("--confirm", action="store_true", help="파일 생성을 확인합니다.")
    pin_init.add_argument("--force", action="store_true", help="기존 registry를 재시딩합니다.")
    pin_init.add_argument("--json", action="store_true")
    pin_init.set_defaults(func=_cmd_pin_init)

    pin_show = pin_subparsers.add_parser(
        "show", help="현재 pin·digest·회전 메타·차단 목록을 출력합니다."
    )
    pin_show.add_argument("--json", action="store_true")
    pin_show.set_defaults(func=_cmd_pin_show)

    pin_verify = pin_subparsers.add_parser(
        "verify", help="digest 재계산·canonical URL·공개 사본 정합을 점검합니다."
    )
    pin_verify.add_argument("--json", action="store_true")
    pin_verify.set_defaults(func=_cmd_pin_verify)

    pin_rotate = pin_subparsers.add_parser(
        "rotate", help="한 role의 revision을 교체하고 digest를 자동 계산합니다."
    )
    pin_rotate.add_argument("--role", required=True, choices=["map", "pinvi"])
    pin_rotate.add_argument("--revision", required=True, help="40-hex commit SHA입니다.")
    pin_rotate.add_argument(
        "--reason",
        required=True,
        help="회전 사유(감사 기록 필수). world-readable 공개 사본에 그대로 기록되므로 비밀을 적지 않습니다.",
    )
    pin_rotate.add_argument(
        "--block-previous",
        action="store_true",
        help="직전 pinset을 terminal로 등재해 재시도를 영구 차단합니다.",
    )
    pin_rotate.add_argument("--confirm", action="store_true")
    pin_rotate.add_argument("--json", action="store_true")
    pin_rotate.set_defaults(func=_cmd_pin_rotate)

    pin_block = pin_subparsers.add_parser(
        "block", help="terminal 판정 pinset을 영구 차단 목록에 등재합니다."
    )
    pin_block.add_argument("pinset", help="차단할 pinset sha256입니다.")
    pin_block.add_argument("--reason", required=True)
    pin_block.add_argument("--map-revision", dest="map_revision")
    pin_block.add_argument("--pinvi-revision", dest="pinvi_revision")
    pin_block.add_argument("--phase", help="이 phase의 journal에서만 차단합니다.")
    pin_block.add_argument("--confirm", action="store_true")
    pin_block.add_argument("--json", action="store_true")
    pin_block.set_defaults(func=_cmd_pin_block)

    pin_rollback = pin_subparsers.add_parser(
        "rollback", help="보존된 이전 registry로 원복합니다(차단 pinset은 거부)."
    )
    pin_rollback.add_argument("--to", required=True, help="원복할 pinset sha256입니다.")
    pin_rollback.add_argument("--reason", required=True)
    pin_rollback.add_argument("--confirm", action="store_true")
    pin_rollback.add_argument("--json", action="store_true")
    pin_rollback.set_defaults(func=_cmd_pin_rollback)

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
