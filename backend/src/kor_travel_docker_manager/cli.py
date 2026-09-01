import argparse
import fcntl
import json
import os
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
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
from kor_travel_docker_manager.services.offbox_backup_sync import (
    OffboxSyncError,
    OffboxSyncNotConfiguredError,
    read_offbox_sync_status,
    sync_backups_offbox,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    publish_pinned_runtime_generation,
    read_manifest,
    read_published_pinned_runtime_generation,
    read_rebuild_journal,
)
from kor_travel_docker_manager.services.registry import list_targets
from kor_travel_docker_manager.services.runtime_execution_registry import (
    block_current_execution,
    load_runtime_execution_registry,
    migrate_execution_registry,
    rebind_execution_registry,
    runtime_execution_registry_path,
    trusted_manager_source_revision,
    verify_runtime_execution_registry,
    write_runtime_execution_registry,
)
from kor_travel_docker_manager.services.runtime_pair_rotation import (
    load_pending_runtime_pair_rotation,
    rollback_with_execution,
    rotate_pair_with_execution,
    rotate_single_role_with_execution,
)
from kor_travel_docker_manager.services.runtime_pin_registry import (
    RuntimePinRegistry,
    block_runtime_pinset,
    build_registry,
    load_runtime_pin_registry,
    packaged_seed_path,
    rollback_runtime_pin,
    rotate_runtime_pin,
    rotate_runtime_pin_pair,
    runtime_pin_registry_path,
    verify_runtime_pin_registry,
    write_runtime_pin_registry,
)
from kor_travel_docker_manager.services.runtime_pin_request import (
    MAX_REASON_LENGTH,
    clear_runtime_pin_request,
    discard_unreadable_runtime_pin_request,
    prospective_pinset_sha256,
    read_runtime_pin_request,
    runtime_pin_request_path,
)
from kor_travel_docker_manager.services.standalone_backup import (
    BACKUP_ROLES,
    StandaloneBackupError,
    StandaloneBackupNotFoundError,
    create_standalone_backup,
    gc_standalone_backups,
    list_standalone_backups,
    plan_standalone_restore,
    rehearse_standalone_restore,
)
from kor_travel_docker_manager.services.trusted_install import (
    GLOBAL_MUTATION_LOCK_FD_ENV,
    GLOBAL_MUTATION_LOCK_PATH,
)

DIRECT_ENSURE_ALIASES = {
    alias for target in list_targets() for alias in [target["id"], *target.get("aliases", [])]
}

# GM-09: c6c_deployment.py의 _C6C_GLOBAL_MUTATION_LOCK과 반드시 같은 파일을 가리켜야
# pinned rebuild와 pin 회전이 서로 직렬화된다 — 정본은 services/trusted_install.py다.
_GLOBAL_MUTATION_LOCK_PATH = GLOBAL_MUTATION_LOCK_PATH
_INHERITED_GLOBAL_MUTATION_LOCK_FD_ENV = GLOBAL_MUTATION_LOCK_FD_ENV


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


def _print_pin_command_failure(detail: str, *, json_output: bool) -> None:
    """pin 계열 명령이 실패했을 때 --json이면 stdout에도 JSON을 낸다.

    `pin show-pending`이 먼저 지킨 계약("--json은 어떤 경로에서도 stdout에 JSON만
    낸다")을 init/show/verify/rotate/rotate-pair/block/rollback/apply-pending도
    지키게 한다 — 그러지 않으면 실패 시 stdout이 비어 `| jq`가 죽는다.

    여기서 넘기는 detail은 전부 이 모듈이나 pin registry 계열 서비스가 조립한
    안전한 문구다 — pinvi-pair rebuild의 unclassified contract 오류(원문
    subprocess 출력을 감쌀 수 있음)와 달리 raw 텍스트를 그대로 노출해도 비밀이
    새지 않는다. 그 다른 경로의 "노출 안 함" 계약은
    test_cli_rebuild_pinned_runtime_hides_unclassified_contract_error_in_json이
    지키므로 여기서 건드리지 않는다.
    """

    if json_output:
        print(json.dumps({"status": "failed", "detail": detail}, ensure_ascii=False))
    print(detail, file=sys.stderr)


def _print_pin_registry_failure(exc: DeploymentContractError, *, json_output: bool) -> None:
    """DeploymentContractError를 _print_pin_command_failure의 detail로 넘긴다."""

    _print_pin_command_failure(str(exc), json_output=json_output)


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
        print("⚠ 현재 고정된 pinset은 legacy source terminal 감사 기록을 가집니다.")
        print("  기존 source candidate를 다시 실행할 수는 없습니다. v6 execution migration 뒤에는")
        print("  'ktdctl pin verify'가 새 trusted implementation의 실행 가능 여부를 판정합니다.")
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
    try:
        with _runtime_pin_mutation_lock():
            # 존재 여부와 history/blocked 목록은 모두 같은 lock 안에서 읽는다. 밖에서
            # 읽으면 다른 회전이 끝난 뒤 stale `--force` reseed가 그 terminal 이력을
            # 지울 수 있다.
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
        _print_pin_registry_failure(exc, json_output=args.json)
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
        _print_pin_registry_failure(exc, json_output=args.json)
        return 2
    _print_registry(registry, json_output=args.json)
    return 0


def _cmd_pin_verify(args: argparse.Namespace) -> int:
    try:
        report = verify_runtime_pin_registry()
        pending_rotation = load_pending_runtime_pair_rotation()
    except DeploymentContractError as exc:
        _print_pin_registry_failure(exc, json_output=args.json)
        return 2
    generation = read_published_pinned_runtime_generation()
    generation_binding = generation.get("pinset_binding")
    binding_status = (
        generation_binding.get("status")
        if isinstance(generation_binding, dict)
        else "unknown"
    )
    if generation.get("status") != "ok":
        generation_public_copy = "invalid"
    elif binding_status == "match":
        generation_public_copy = "current"
    elif binding_status == "pending_rebuild":
        # pair를 회전한 직후의 last committed generation은 정상적으로 이전 pinset을
        # 가리킨다. strict public documents가 모두 유효하다는 사실과 one-shot 전의
        # 새 pair 상태를 구분해 보여 주되, 이를 current generation이라고 부르지 않는다.
        generation_public_copy = "pending_rebuild"
    else:
        generation_public_copy = "invalid"
    report["generation_public_copy"] = generation_public_copy
    report["generation_pinset_binding"] = binding_status
    execution_binding = "missing"
    execution_terminal = True
    execution_public_copy = "missing"
    execution_source_pinset: str | None = None
    registry_blocks_execution_source = False
    try:
        execution_registry = load_runtime_execution_registry()
        execution_report = verify_runtime_execution_registry()
        execution_public_copy = str(execution_report["execution_public_copy"])
        current_pins = load_runtime_pin_registry()
        execution_source_pinset = execution_registry.current.source_pinset_sha256
        # execution이 가리키는 pinset이 registry에서 terminal이면 rollback --to 그 pinset이
        # 거부되므로, stale 안내가 rollback을 주면 안 된다(아래에서 rotate-pair로 유도).
        registry_blocks_execution_source = current_pins.is_blocked_pinset(
            execution_source_pinset
        )
        if execution_registry.current_matches(
            pins=current_pins,
            manager_source_revision=trusted_manager_source_revision(),
        ):
            execution_binding = "current"
        elif (
            execution_registry.current.source_pinset_sha256 == current_pins.pinset_sha256
            and execution_registry.current.map_revision == current_pins.map_revision
            and execution_registry.current.pinvi_revision == current_pins.pinvi_revision
        ):
            # source(pinset·두 revision)는 일치하고 trusted Manager revision만 달라진
            # 상태다 — trusted Manager release 업그레이드의 표준 경로. 해법은 rebind이지
            # rollback/rotate-pair가 아니다(그 둘은 불필요한 source SHA 변경을 강제하고,
            # rollback --to <현재 pinset>은 "already uses this pinset"으로 항상 실패한다).
            execution_binding = "manager_drift"
        else:
            execution_binding = "stale"
        execution_terminal = execution_registry.is_unconditionally_blocked_current()
    except DeploymentContractError:
        execution_binding = "invalid"
    report["execution_binding"] = execution_binding
    report["current_execution_is_blocked"] = execution_terminal
    report["execution_public_copy"] = execution_public_copy
    report["pair_rotation"] = "pending" if pending_rotation is not None else "idle"
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for key, value in report.items():
            print(f"{key:24s} {value}")
    exit_code = 0
    if report.get("current_pinset_is_blocked") and execution_binding != "current":
        print(
            "현재 고정된 pinset은 재시도 금지 상태입니다 — rebuild-pinned가 거부됩니다. "
            "'ktdctl pin rotate-pair'로 새 Map/PinVi pair를 고정하세요.",
            file=sys.stderr,
        )
        exit_code = 1
    if execution_binding == "manager_drift":
        # source는 그대로고 trusted Manager revision만 바뀐 표준 업그레이드 경로.
        # rebind가 정본이다(2026-08-29 ADR). rollback/rotate-pair를 안내하면 불필요한
        # source SHA 변경을 강제하고, rollback --to <현재 pinset>은 항상 거부된다.
        print(
            "trusted Manager release가 바뀌어 execution binding이 갱신되어야 합니다. "
            "복구: 'ktdctl pin rebind-execution'으로 현재 source pinset에 새 Manager "
            "release를 재결박하세요(rebuild·source 회전 불필요).",
            file=sys.stderr,
        )
        exit_code = 1
    elif execution_binding == "stale":
        # stale에는 여러 하위 상태가 있고, 잘못된 안내는 항상 거부되는 명령을 준다.
        if pending_rotation is not None:
            # v5는 썼고 v6는 못 쓴 crash 창이다. rollback/rotate-pair는 durable
            # intent가 막아 둘 다 거부된다 — 실제 복구는 같은 명령의 재실행이다.
            print(
                "직전 pair 회전이 중단돼 durable intent가 남아 있습니다. 복구: "
                "중단된 그 명령(pin rotate-pair 또는 apply-pending)을 같은 인자로 다시 "
                "실행하면 미완 transaction이 끝까지 반영됩니다.",
                file=sys.stderr,
            )
        elif execution_source_pinset is not None and registry_blocks_execution_source:
            # execution이 가리키는 pinset이 terminal이라 rollback이 거부된다.
            # 유일한 전진 경로는 새 pair 회전이다.
            print(
                "execution이 가리키는 source pinset이 재시도 금지 상태라 rollback이 "
                "거부됩니다. 'ktdctl pin rotate-pair'로 새 Map/PinVi pair를 고정하세요.",
                file=sys.stderr,
            )
        else:
            # source(pinset 또는 revision)가 실제로 어긋난 일반 경우. rebind는 동일
            # source pinset 전용이라 이 상태를 못 고친다.
            print(
                "runtime execution binding이 source registry와 어긋납니다. 복구: "
                f"'ktdctl pin rollback --to {execution_source_pinset}'으로 source를 "
                "execution이 가리키는 pinset으로 되돌리거나(치유형 — execution은 보존), "
                "'ktdctl pin rotate-pair'로 새 pair를 고정하세요.",
                file=sys.stderr,
            )
        exit_code = 1
    elif execution_binding != "current":
        print(
            "runtime execution binding is missing or invalid; root must migrate "
            "('ktdctl pin migrate-execution-v6') or repair it before a runtime mutation",
            file=sys.stderr,
        )
        exit_code = 1
    elif execution_terminal:
        print(
            "현재 Manager-aware execution은 재시도 금지 상태입니다 — 새 trusted Manager "
            "release 뒤 'ktdctl pin rebind-execution'으로만 새 실행을 결박하세요.",
            file=sys.stderr,
        )
        exit_code = 1
    if execution_public_copy != "current":
        print(
            "runtime execution public copy is missing, malformed, or stale; root must "
            "repair the execution binding before a runtime mutation",
            file=sys.stderr,
        )
        exit_code = 1
    if pending_rotation is not None:
        print(
            "runtime pair rotation is incomplete; resume the same root 'ktdctl pin "
            "rotate-pair' command before a runtime mutation",
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
    if generation_public_copy == "invalid":
        print(
            "pinned runtime generation public copy is incomplete, malformed, or does not "
            "bind to the current registry pair",
            file=sys.stderr,
        )
        exit_code = 1
    elif generation_public_copy == "pending_rebuild":
        print(
            "pinned runtime generation is a valid previous committed pair; the rotated "
            "pair still requires its one-shot rebuild",
            file=sys.stderr,
        )
    return exit_code


def _print_execution_registry(registry: Any, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(registry.to_payload(), ensure_ascii=False, indent=2))
        return
    current = registry.current
    print(f"execution  {current.execution_identity_sha256}")
    print(f"source-pin {current.source_pinset_sha256}")
    print(f"manager    {current.manager_source_revision}")
    print(f"bound      {current.bound_at} by {current.bound_by}")
    print(f"reason     {current.reason}")
    print(f"terminal   {registry.is_unconditionally_blocked_current()}")


def _cmd_pin_migrate_execution(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("pin migrate-execution-v6 requires --confirm", file=sys.stderr)
        return 2
    if not _running_as_root():
        print("pin migrate-execution-v6 requires root", file=sys.stderr)
        return 2
    try:
        with _runtime_pin_mutation_lock():
            try:
                load_runtime_execution_registry()
            except DeploymentContractError:
                pass
            else:
                print("runtime execution registry already exists; migration is refused", file=sys.stderr)
                return 2
            pins = load_runtime_pin_registry()
            # v5 terminal에는 Manager revision provenance가 없다. 기존 source audit은
            # 그대로 두고, 이 trusted release의 새 v6 execution만 생성한다. 그래서
            # legacy verdict를 임의의 과거 Manager execution으로 위조하지 않는다.
            registry = migrate_execution_registry(
                pins=pins,
                manager_source_revision=trusted_manager_source_revision(),
                bound_by=_pin_actor(),
                reason=args.reason,
            )
            write_runtime_execution_registry(registry)
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_execution_registry(registry, json_output=args.json)
    return 0


def _cmd_pin_rebind_execution(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("pin rebind-execution requires --confirm", file=sys.stderr)
        return 2
    if not _running_as_root():
        print("pin rebind-execution requires root", file=sys.stderr)
        return 2
    try:
        with _runtime_pin_mutation_lock():
            trusted_revision = trusted_manager_source_revision()
            if args.expected_manager_revision != trusted_revision:
                print(
                    "expected Manager revision differs from the trusted installed release",
                    file=sys.stderr,
                )
                return 2
            registry = rebind_execution_registry(
                registry=load_runtime_execution_registry(),
                pins=load_runtime_pin_registry(),
                manager_source_revision=trusted_revision,
                bound_by=_pin_actor(),
                reason=args.reason,
            )
            write_runtime_execution_registry(registry)
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_execution_registry(registry, json_output=args.json)
    return 0


def _cmd_pin_show_execution(args: argparse.Namespace) -> int:
    try:
        registry = load_runtime_execution_registry()
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_execution_registry(registry, json_output=args.json)
    return 0


def _cmd_pin_block_execution(args: argparse.Namespace) -> int:
    """현재 verified execution만 idempotent하게 terminal 처리한다."""

    if not args.confirm:
        print("pin block-execution requires --confirm", file=sys.stderr)
        return 2
    if not _running_as_root():
        print("pin block-execution requires root", file=sys.stderr)
        return 2
    try:
        with _runtime_pin_mutation_lock(allow_inherited_terminal_block=True):
            pins = load_runtime_pin_registry()
            registry = load_runtime_execution_registry()
            trusted_revision = trusted_manager_source_revision()
            if not registry.current_matches(
                pins=pins, manager_source_revision=trusted_revision
            ):
                print("current runtime execution binding is stale", file=sys.stderr)
                return 2
            updated = block_current_execution(
                registry=registry, reason=args.reason, phase=args.phase
            )
            write_runtime_execution_registry(updated)
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_execution_registry(updated, json_output=args.json)
    return 0


def _cmd_pin_publish_generation(args: argparse.Namespace) -> int:
    """root private state를 검증한 뒤 API용 public copy만 갱신한다."""

    if not args.confirm:
        print(
            "pin publish-generation requires --confirm (no public copy was written)",
            file=sys.stderr,
        )
        return 2
    if not _running_as_root():
        print("pin publish-generation requires root", file=sys.stderr)
        return 2
    manifest_path = Path(args.manifest)
    journal_path = Path(args.journal)
    if not manifest_path.is_absolute() or not journal_path.is_absolute():
        print("manifest and journal paths must be absolute", file=sys.stderr)
        return 2
    try:
        with _runtime_pin_mutation_lock():
            paths = publish_pinned_runtime_generation(
                manifest=read_manifest(manifest_path),
                journal=read_rebuild_journal(journal_path),
            )
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    generation = read_published_pinned_runtime_generation()
    binding = generation.get("pinset_binding")
    binding_status = binding.get("status") if isinstance(binding, dict) else "unknown"
    published = generation.get("status") == "ok" and binding_status == "match"
    payload = {
        "status": "published" if published else "unverified",
        "manifest_public_path_name": paths.manifest.name,
        "journal_public_path_name": paths.journal.name,
        "pinset_binding": binding_status,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            "pinned runtime generation public copy published"
            if published
            else "pinned runtime generation public copy is not the current registry pair",
            file=None if published else sys.stderr,
        )
    return 0 if published else 1


def _print_rotation_write_failure(exc: OSError, *, json_output: bool) -> None:
    """registry write 도중의 I/O 실패를 traceback 없이 복구 경로와 함께 보고한다.

    durable intent가 남아 있으므로 같은 명령 재실행이 미완 transaction을 재개한다 —
    이를 모르면 운영자는 절반 기록된 상태를 수동으로 수술하려 든다. --json이면
    이 실패도 stdout에 {"status": "failed", ...}로 낸다 — DeploymentContractError만
    그 계약을 지키고 OSError는 예외로 남으면 자동화가 성공/실패를 구분하지 못한다.
    """

    detail = f"registry write failed: {exc}"
    if json_output:
        print(json.dumps({"status": "failed", "detail": detail}, ensure_ascii=False))
    print(detail, file=sys.stderr)
    print(
        "durable intent가 남아 있으면 같은 명령을 다시 실행해 미완 transaction을 "
        "재개하세요. 'ktdctl pin verify'로 현재 상태를 확인할 수 있습니다.",
        file=sys.stderr,
    )


def _uses_execution_registry() -> bool:
    """Manager-aware v6 execution registry를 가진 host인지 판별한다.

    v6 파일이 존재하는데 읽기 불가면 legacy host로 오인해 v5만 회전하는 일이 없도록
    예외를 전파한다.
    """

    try:
        load_runtime_execution_registry()
    except DeploymentContractError:
        # lexists: `.exists()`는 symlink를 따라가므로 dangling symlink에서 False가 되어
        # v6 host를 legacy로 오인(fail-open)한다. 링크 자체의 존재를 본다.
        if os.path.lexists(runtime_execution_registry_path()):
            raise
        return False
    return True


def _rotate_single_role_source(
    *, role: str, revision: str, reason: str, rotated_by: str, block_previous: bool
) -> RuntimePinRegistry:
    """단일 role 회전을 host 세대에 맞는 writer로 보낸다.

    v6 host에서 v5만 회전하면 execution binding이 stale이 되어 모든 runtime mutation이
    fail-close된다. 그래서 v6 host는 durable intent 아래 v5/v6를 함께 회전하고,
    pending intent가 남아 있으면 같은 target의 재개만 transaction helper가 허용한다.
    legacy host는 기존 v5 단독 회전을 유지한다.
    """

    if load_pending_runtime_pair_rotation() is not None or _uses_execution_registry():
        return rotate_single_role_with_execution(
            role=role,
            revision=revision,
            manager_source_revision=trusted_manager_source_revision(),
            reason=reason,
            rotated_by=rotated_by,
            block_previous=block_previous,
        )
    return rotate_runtime_pin(
        role=role,
        revision=revision,
        reason=reason,
        rotated_by=rotated_by,
        block_previous=block_previous,
    )


def _cmd_pin_rotate(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("pin rotate requires --confirm (no file was written)", file=sys.stderr)
        return 2
    try:
        with _runtime_pin_mutation_lock(allow_pending_pair_recovery=True):
            registry = _rotate_single_role_source(
                role=args.role,
                revision=args.revision,
                reason=args.reason,
                rotated_by=_pin_actor(),
                block_previous=args.block_previous,
            )
    except DeploymentContractError as exc:
        _print_pin_registry_failure(exc, json_output=args.json)
        return 2
    except OSError as exc:
        _print_rotation_write_failure(exc, json_output=args.json)
        return 2
    print(f"rotated {args.role} pin; new pinset {registry.pinset_sha256}")
    _print_registry(registry, json_output=args.json)
    return 0


def _cmd_pin_rotate_pair(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("pin rotate-pair requires --confirm (no file was written)", file=sys.stderr)
        return 2
    try:
        with _runtime_pin_mutation_lock(allow_pending_pair_recovery=True):
            # v6 private 파일이 partial write에서 사라졌더라도 durable intent가 있으면
            # 이를 legacy host로 오인하면 안 된다. recovery helper만 exact target을
            # 허용하므로 intent를 먼저 판별해 같은 pair 재개/다른 pair 거부를 보장한다.
            if load_pending_runtime_pair_rotation() is not None:
                registry = rotate_pair_with_execution(
                    map_revision=args.map_revision,
                    pinvi_revision=args.pinvi_revision,
                    manager_source_revision=trusted_manager_source_revision(),
                    reason=args.reason,
                    rotated_by=_pin_actor(),
                    block_previous=args.block_previous,
                )
                print(f"rotated Map/PinVi pair; new pinset {registry.pinset_sha256}")
                _print_registry(registry, json_output=args.json)
                return 0
            if not _uses_execution_registry():
                # v6 migration 전 host는 source registry만 회전한다. migration command가
                # 해당 source pair의 첫 execution을 별도로 만든다.
                registry = rotate_runtime_pin_pair(
                    map_revision=args.map_revision,
                    pinvi_revision=args.pinvi_revision,
                    reason=args.reason,
                    rotated_by=_pin_actor(),
                    block_previous=args.block_previous,
                )
            else:
                # v6 host는 별도 v5/v6 파일을 순차적으로 "성공" 처리하지 않는다.
                # helper가 durable intent·idempotent recovery를 소유하며, 여기서는
                # legacy 여부만 판별한다.
                registry = rotate_pair_with_execution(
                    map_revision=args.map_revision,
                    pinvi_revision=args.pinvi_revision,
                    manager_source_revision=trusted_manager_source_revision(),
                    reason=args.reason,
                    rotated_by=_pin_actor(),
                    block_previous=args.block_previous,
                )
    except DeploymentContractError as exc:
        _print_pin_registry_failure(exc, json_output=args.json)
        return 2
    except OSError as exc:
        _print_rotation_write_failure(exc, json_output=args.json)
        return 2
    print(f"rotated Map/PinVi pair; new pinset {registry.pinset_sha256}")
    _print_registry(registry, json_output=args.json)
    return 0


def _cmd_pin_block(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("pin block requires --confirm (no file was written)", file=sys.stderr)
        return 2
    if not _running_as_root():
        print("pin block requires root", file=sys.stderr)
        return 2
    try:
        with _runtime_pin_mutation_lock():
            registry = block_runtime_pinset(
                pinset_sha256=args.pinset,
                reason=args.reason,
                map_revision=args.map_revision,
                pinvi_revision=args.pinvi_revision,
                phase=args.phase,
            )
    except DeploymentContractError as exc:
        _print_pin_registry_failure(exc, json_output=args.json)
        return 2
    print(f"pinset {args.pinset} is now permanently blocked")
    _print_registry(registry, json_output=args.json)
    return 0


def _pending_request_banner(request: Any, registry: Any) -> str | None:
    """요청이 만들어진 뒤 pin이 바뀌었으면 그 사실을 먼저 말한다."""

    if registry is None or request.base_pinset_sha256 == registry.pinset_sha256:
        return None
    return (
        "⚠ 요청이 만들어진 이후 pin이 바뀌었습니다(요청 base "
        f"{request.base_pinset_sha256[:12]}... vs 현재 "
        f"{registry.pinset_sha256[:12]}...). 이 요청은 적용되지 않습니다."
    )


def _running_as_root() -> bool:
    """registry는 root `0600`이라 이 판정이 apply-pending의 첫 관문이다.

    `geteuid`가 없는 플랫폼(Windows)에서는 판정하지 않고 통과시킨다 — 그때는 registry
    쓰기 자체가 뒤에서 실패하므로, 여기서 막으면 오해를 부르는 메시지만 남는다.
    """

    geteuid = getattr(os, "geteuid", None)
    return geteuid is None or geteuid() == 0


@contextmanager
def _runtime_pin_mutation_lock(
    *,
    allow_inherited_terminal_block: bool = False,
    allow_pending_pair_recovery: bool = False,
) -> Iterator[None]:
    """모든 runtime pin mutation을 one-shot과 직렬화한다.

    one-shot launcher의 terminal fallback만 검증한 inherited descriptor로 재진입할 수
    있다. 그 밖의 `init`·공개 복사·회전·rollback·block은 별도 SSH/CLI 호출이므로 lock이
    비어 있을 때만 수행한다. 따라서 출력 회수 지연을 실패로 오인해 실행 중 candidate를
    봉인하거나 pair/generation을 바꿀 수 없다. pair+execution durable intent가 남아 있으면
    모든 mutation을 거부한다. 예외는 동일 target을 끝까지 publish하는 recovery뿐이다 —
    ``rotate-pair``와 v6 host의 단일 role ``rotate``/``apply-pending``/``rollback``이
    해당하며, 그 target 대조는 transaction helper가 소유한다.
    """

    def reject_pending_pair_rotation() -> None:
        if allow_pending_pair_recovery:
            return
        from kor_travel_docker_manager.services.runtime_pair_rotation import (
            require_no_pending_runtime_pair_rotation,
        )

        require_no_pending_runtime_pair_rotation()

    inherited_text = os.environ.get(_INHERITED_GLOBAL_MUTATION_LOCK_FD_ENV, "")
    if inherited_text:
        if not allow_inherited_terminal_block or not inherited_text.isdecimal():
            raise DeploymentContractError("runtime pin mutation inherited lock is invalid")
        descriptor = int(inherited_text)
        try:
            opened = os.fstat(descriptor)
            named = _GLOBAL_MUTATION_LOCK_PATH.lstat()
        except OSError as exc:
            raise DeploymentContractError("runtime pin mutation inherited lock is invalid") from exc
        if (
            (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            # `_cmd_pin_block` 자체가 root 전용이므로 production에서는 root FD만
            # 수용한다. test/local에서는 실행 uid와의 동등성으로 같은 ownership
            # invariant를 확인한다.
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
        ):
            raise DeploymentContractError("runtime pin mutation inherited lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeploymentContractError("runtime pin mutation inherited lock is not held") from exc
        reject_pending_pair_rotation()
        yield
        return

    try:
        descriptor = os.open(
            _GLOBAL_MUTATION_LOCK_PATH,
            os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except FileNotFoundError:
        # 개발·테스트처럼 launcher를 한 번도 실행하지 않은 환경에는 active global
        # mutation이 없으므로, registry 자체의 root ownership gate에 맡긴다.
        reject_pending_pair_rotation()
        yield
        return
    except PermissionError:
        # production lock directory는 root `0700`이다. 비root 개발 fixture는 registry를
        # 임시 경로로 바꿔 검증하므로 이 host lock을 열 수 없고, 실제 production에서는
        # 이어지는 root-owned registry write 자체가 거절된다. 따라서 권한 없는 개발
        # 호출을 active mutation으로 오인하지 않는다.
        if getattr(os, "geteuid", lambda: 1)() != 0:
            reject_pending_pair_rotation()
            yield
            return
        raise DeploymentContractError("runtime pin mutation lock is unavailable") from None
    except OSError as exc:
        raise DeploymentContractError("runtime pin mutation lock is unavailable") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeploymentContractError(
                "runtime pin mutation is refused while a Manager mutation is active"
            ) from exc
        reject_pending_pair_rotation()
        yield
    finally:
        os.close(descriptor)


_ACTOR_LENGTH_LIMIT = 200


def _registry_or_none() -> Any:
    try:
        return load_runtime_pin_registry()
    except DeploymentContractError:
        return None


def _cmd_db_backup_restore_plan(args: argparse.Namespace) -> int:
    """복원 **계획**만 출력한다. 파일도 DB도 컨테이너도 건드리지 않는다.

    복원 자체는 아직 없다. 이것을 먼저 만드는 이유는, 목록에 백업이 보이는 것과 그
    백업으로 실제 복원할 수 있는 것이 다르기 때문이다 — dump가 잘려 있어도, digest가
    어긋나도, live schema가 백업 시점과 달라도 목록은 똑같이 초록색이다.
    """

    try:
        plan = plan_standalone_restore(args.role, backup_filename=args.file)
    except StandaloneBackupError as exc:
        # --json은 어떤 경로에서도 stdout에 JSON만 낸다 — 같은 파일의 `pin show-pending`이
        # 이미 그 계약을 지킨다. 여기서 빈 stdout을 주면 `| jq`가 죽는다.
        if args.json:
            print(
                json.dumps(
                    {"status": "unavailable", "detail": str(exc)}, ensure_ascii=False
                )
            )
        print(str(exc), file=sys.stderr)
        # "복원할 백업이 없다"는 도구 오류가 아니라 판정 결과다 — exit 1로 낸다.
        # 문구가 아니라 타입으로 판정한다(문구를 다듬으면 판정이 조용히 어긋나는
        # 문자열 매칭을 대체).
        return 1 if isinstance(exc, StandaloneBackupNotFoundError) else 2
    if args.json:
        print(json.dumps(plan.to_json(), ensure_ascii=False, indent=2))
    else:
        manifest = plan.manifest
        print(f"대상      {plan.role} · {plan.backup_filename}")
        print(f"파일      {plan.dump_path}")
        print(f"크기      {manifest.byte_size} bytes (manifest 기준)")
        print(f"sha256    {manifest.sha256}")
        if plan.observed_sha256 is not None:
            match = "일치" if plan.observed_sha256 == manifest.sha256 else "불일치"
            print(f"실측      {plan.observed_sha256} ({match})")
        print(f"백업 시점 schema  {manifest.alembic_head or '알 수 없음'}")
        print(f"현재 schema       {plan.live_alembic_head or '알 수 없음'}")
        if plan.containers:
            print(f"영향 컨테이너     {', '.join(plan.containers)}")
        print()
        for finding in plan.findings:
            marker = "차단" if finding.blocking else "참고"
            print(f"  [{marker}] {finding.text}")
        print()
        advisories = [f for f in plan.findings if not f.blocking and f.code != "OK"]
        if plan.restorable and advisories:
            # 차단은 아니지만 "그냥 괜찮다"고 끝내면 바로 위에 적은 schema 되돌림 경고를
            # 마지막 문장이 지워 버린다.
            print(
                "무결성은 확인됐지만 위 [참고] 항목을 읽고 판단해야 합니다. "
                "'db-backup rehearse-restore'로 scratch DB 복원을 검증할 수 있습니다."
            )
        elif plan.restorable:
            print(
                "이 백업은 복원 가능한 상태로 보입니다. "
                "'db-backup rehearse-restore'로 scratch DB 복원을 검증할 수 있습니다."
            )
        else:
            print("이 백업으로는 복원하면 안 됩니다.")
    # 차단 요인이 있으면 비정상 종료로 알린다 — 스크립트에서 게이트로 쓸 수 있다.
    return 0 if plan.restorable else 1


def _cmd_db_backup_rehearse_restore(args: argparse.Namespace) -> int:
    """백업을 scratch DB에 실제로 복원해 검증한다. 운영 DB는 절대 건드리지 않는다.

    실제 role DB로 덮어쓰는 파괴적 복원은 아직 없다 — 그 결정은 오너가 이미 로드맵
    뒤로 미뤄 두었다(docs/general-mgmt-audit.md GM-07 검증 노트). 여기서 증명하는
    것은 "이 백업이 실제로 복원 가능하다"는 사실뿐이다.
    """

    try:
        outcome = rehearse_standalone_restore(
            args.role, backup_filename=args.file, timeout=args.timeout
        )
    except StandaloneBackupError as exc:
        if args.json:
            print(
                json.dumps(
                    {"status": "unavailable", "detail": str(exc)}, ensure_ascii=False
                )
            )
        print(str(exc), file=sys.stderr)
        return 1 if isinstance(exc, StandaloneBackupNotFoundError) else 2
    if args.json:
        print(json.dumps(outcome.to_json(), ensure_ascii=False, indent=2))
    else:
        print(f"대상      {outcome.role} · {outcome.backup_filename}")
        if not outcome.attempted:
            print("리허설을 시도하지 않았습니다 — 복원 계획에 차단 사유가 있습니다.")
        else:
            print(f"scratch DB  {outcome.scratch_database} (검증 후 삭제됨)")
            print(f"pg_restore  {'성공' if outcome.restore_succeeded else '실패'}")
            if outcome.restored_alembic_head is not None:
                print(f"복원된 schema  {outcome.restored_alembic_head}")
            if outcome.restored_db_size_bytes is not None:
                print(f"복원된 크기    {outcome.restored_db_size_bytes} bytes")
            if outcome.duration_sec is not None:
                print(f"소요 시간      {outcome.duration_sec}s")
        print()
        for finding in outcome.findings:
            marker = "차단" if finding.blocking else "참고"
            print(f"  [{marker}] {finding.text}")
        print()
        if outcome.verified:
            print("이 백업은 scratch DB 복원으로 검증됐습니다.")
        else:
            print("이 백업은 복원 가능함이 아직 증명되지 않았습니다.")
    return 0 if outcome.verified else 1


def _cmd_offbox_sync_run(args: argparse.Namespace) -> int:
    """설정된 원격 호스트로 백업과 pin registry 보존본을 옮기고 원격에서 재검증한다.

    pin registry 파일은 root `0600`이라 이 명령은 root 실행을 요구한다.
    """

    if not _running_as_root():
        print("offbox-sync run requires root (pin registry is root-owned 0600)", file=sys.stderr)
        return 2
    try:
        outcome = sync_backups_offbox(
            include_pin_registry=not args.skip_pin_registry, timeout=args.timeout
        )
    except OffboxSyncError as exc:
        if args.json:
            print(json.dumps({"status": "failed", "detail": str(exc)}, ensure_ascii=False))
        print(str(exc), file=sys.stderr)
        return 1 if isinstance(exc, OffboxSyncNotConfiguredError) else 2
    if args.json:
        print(json.dumps(outcome.to_json(), ensure_ascii=False, indent=2))
    else:
        print(f"대상 호스트  {outcome.destination_host}")
        for target in outcome.targets:
            status = "검증됨" if target.verified else ("전송됨" if target.synced else "실패")
            print(f"  [{status}] {target.label}: {target.detail}")
        print()
        if outcome.all_verified:
            print("모든 대상이 원격에서 sha256sum -c로 재검증됐습니다.")
        else:
            print("일부 대상이 전송·검증되지 않았습니다 — 위 목록을 확인하세요.")
    return 0 if outcome.all_verified else 1


def _cmd_offbox_sync_status(args: argparse.Namespace) -> int:
    """마지막 `offbox-sync run` 결과를 읽기만 한다(root 불필요, 상태 파일은 0644)."""

    status = read_offbox_sync_status()
    if status is None:
        if args.json:
            print(json.dumps({"status": "never_run"}, ensure_ascii=False))
        else:
            print("off-box 동기화를 아직 실행한 적이 없습니다.")
        return 1
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(f"마지막 실행  {status.get('started_at_unix')} (unix)")
        print(f"대상 호스트  {status.get('destination_host')}")
        print(f"전체 검증됨  {status.get('all_verified')}")
        for target in status.get("targets", []):
            print(f"  {target.get('label')}: verified={target.get('verified')}")
    return 0 if status.get("all_verified") else 1


def _cmd_pin_show_pending(args: argparse.Namespace) -> int:
    try:
        request = read_runtime_pin_request()
    except DeploymentContractError as exc:
        if args.json:
            # --json은 어떤 경로에서도 stdout에 JSON만 낸다 — 스크립트가 파싱한다.
            print(json.dumps({"status": "unreadable", "detail": str(exc)}, ensure_ascii=False))
        print(str(exc), file=sys.stderr)
        print(
            "손상된 요청 파일은 'ktdctl pin clear-pending --force --confirm'으로 지웁니다.",
            file=sys.stderr,
        )
        return 2
    if request is None:
        if args.json:
            print(json.dumps({"status": "absent"}, ensure_ascii=False))
        else:
            print("대기 중인 회전 요청이 없습니다.")
        return 1
    registry = _registry_or_none()
    stale = registry is not None and request.base_pinset_sha256 != registry.pinset_sha256
    if args.json:
        # staleness는 사람 화면에만 있던 정보였다. 스크립트도 같은 판정을 볼 수 있어야
        # base 대조를 각자 다시 구현하지 않는다.
        print(
            json.dumps(
                {"status": "stale" if stale else "pending", **request.to_payload()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    banner = _pending_request_banner(request, registry)
    if banner:
        print(banner)
        print()
    print(f"요청 id   {request.request_id}")
    print(f"대상      {request.role}")
    print(f"revision  {request.revision}")
    print(f"요청자    {request.requested_by} ({request.requested_at})")
    print(f"사유      {request.reason}")
    print(f"적용 후   pinset {request.prospective_pinset_sha256}")
    print(f"파일      {runtime_pin_request_path()}")
    print()
    print(f"적용: ktdctl pin apply-pending --expect-revision {request.revision} --confirm")
    return 0


def _applied_actor(request: Any) -> str:
    """누가 요청하고 누가 적용했는지를 한 줄에 함께 남긴다.

    길이가 넘치면 **적용자 쪽이 아니라 요청자 문자열**을 줄인다. 뒤에서 자르면 적용자만
    남고 "누가 제안했는가"가 통째로 사라진다.
    """

    applier = _pin_actor().replace("\n", " ").replace("\r", " ")
    requester = request.requested_by.replace("\n", " ").replace("\r", " ")
    budget = _ACTOR_LENGTH_LIMIT - len(applier) - len("<-")
    if budget < 1:
        return applier[:_ACTOR_LENGTH_LIMIT]
    return f"{applier}<-{requester[:budget]}"


def _applied_reason(request: Any) -> str:
    """사유와 출처를 함께 남기되, 넘치면 **사유 쪽을** 줄인다.

    요청 사유는 최대 500자이고 registry의 reason 상한도 500자라, 그냥 이어 붙인 뒤
    뒤를 자르면 요청 id·요청자·시각이 통째로 잘려 나간다. 그러면 registry 이력에서
    이 회전이 어느 요청에서 왔는지 되짚을 수 없다 — 2-step의 감사 가치가 사라진다.
    """

    provenance = (
        f" (UI 요청 {request.request_id}, 요청자 {request.requested_by}, "
        f"요청 시각 {request.requested_at})"
    ).replace("\n", " ").replace("\r", " ")
    budget = MAX_REASON_LENGTH - len(provenance)
    reason = request.reason.replace("\n", " ").replace("\r", " ")
    if budget < 1:
        return provenance[:MAX_REASON_LENGTH]
    if len(reason) > budget:
        reason = f"{reason[: max(budget - 1, 0)]}…"
    return f"{reason}{provenance}"


def _cmd_pin_apply_pending(args: argparse.Namespace) -> int:
    """대기 요청의 read·검증·회전·정리를 하나의 mutation lock으로 감싼다."""

    if not args.confirm:
        print("pin apply-pending requires --confirm (no file was written)", file=sys.stderr)
        return 2
    if not _running_as_root():
        print(
            "pin apply-pending requires root execution (the registry is root-owned 0600)",
            file=sys.stderr,
        )
        return 2
    if not args.expect_revision and not args.any_revision:
        print(
            "pin apply-pending requires --expect-revision <40-hex> (or --any-revision to "
            "apply whatever is pending); run 'ktdctl pin show-pending' first",
            file=sys.stderr,
        )
        return 2
    try:
        # request/base/prospective 검증과 요청 파일 정리까지 같은 경계 안에 둔다.
        # lock 밖에서 읽으면 다른 회전 직후 stale 요청을 새 pair에 적용할 수 있다.
        # 직전 apply가 v5/v6 사이에서 중단됐다면 같은 target의 재개를 허용해야 하므로
        # pending intent recovery를 연다 — target 대조는 transaction helper가 소유한다.
        with _runtime_pin_mutation_lock(allow_pending_pair_recovery=True):
            return _cmd_pin_apply_pending_locked(args)
    except DeploymentContractError as exc:
        _print_pin_registry_failure(exc, json_output=args.json)
        return 2


def _cmd_pin_apply_pending_locked(args: argparse.Namespace) -> int:
    """UI가 남긴 요청을 root 권한으로 적용한다.

    요청에서 취하는 것은 role과 40-hex revision, 표시용 문자열뿐이다. canonical URL과
    digest, 차단 목록은 전부 코드와 root registry에서 다시 만든다.
    """

    if not args.confirm:
        print("pin apply-pending requires --confirm (no file was written)", file=sys.stderr)
        return 2
    # registry 쓰기와 backend 소유 요청 파일 삭제를 함께 하므로, 권한 부족을 절반쯤
    # 진행한 뒤에 알게 되면 안 된다.
    if not _running_as_root():
        print(
            "pin apply-pending requires root execution (the registry is root-owned 0600)",
            file=sys.stderr,
        )
        return 2
    # revision을 명시하지 않으면 "파일에 들어 있는 것"을 그대로 적용하게 된다. 그 사이
    # 요청이 바뀌었을 수 있으므로, 무엇을 고정하는지 손으로 적었거나 적지 않기로
    # 명시했을 때만 진행한다.
    if not args.expect_revision and not args.any_revision:
        print(
            "pin apply-pending requires --expect-revision <40-hex> (or --any-revision to "
            "apply whatever is pending); run 'ktdctl pin show-pending' first",
            file=sys.stderr,
        )
        return 2
    try:
        request = read_runtime_pin_request()
    except DeploymentContractError as exc:
        _print_pin_command_failure(str(exc), json_output=args.json)
        print(
            "손상된 요청 파일은 'ktdctl pin clear-pending --force --confirm'으로 지웁니다.",
            file=sys.stderr,
        )
        return 2
    if request is None:
        # show-pending과 같은 "absent" 어휘를 쓴다 — 실패가 아니라 상태 보고다.
        if args.json:
            print(json.dumps({"status": "absent"}, ensure_ascii=False))
        else:
            print("대기 중인 회전 요청이 없습니다.")
        return 1
    try:
        registry = load_runtime_pin_registry()
    except DeploymentContractError as exc:
        _print_pin_registry_failure(exc, json_output=args.json)
        return 2

    if args.expect_revision and args.expect_revision != request.revision:
        _print_pin_command_failure(
            "pending request revision does not match --expect-revision",
            json_output=args.json,
        )
        return 2

    # 직전 apply가 v5 write와 v6 write 사이에서 중단됐다면 registry는 이미 회전 후
    # 상태다 — base/prospective/no-change 검증은 회전 전 상태를 전제하므로 재개
    # 경로에서는 건너뛴다. 단 **그 intent가 이 요청의 것인지**는 반드시 확인한다.
    # 그러지 않으면 운영자의 pair 회전이 v5 write 뒤 crash한 상태에서 남아 있던
    # 무관한 단일 role 요청을 apply-pending이 "적용됨"으로 소비하고, 실제로는 요청이
    # 선언하지 않은 pair 결과를 그 요청에 귀속시킨다.
    pending_rotation = load_pending_runtime_pair_rotation()
    if pending_rotation is not None:
        if pending_rotation.pin_registry.pinset_sha256 != request.prospective_pinset_sha256:
            _print_pin_command_failure(
                "대기 중인 transaction이 이 요청과 다른 pinset을 향합니다"
                f"(intent {pending_rotation.pin_registry.pinset_sha256[:12]}... vs 요청 "
                f"{request.prospective_pinset_sha256[:12]}...). "
                "먼저 해당 transaction을 같은 명령으로 재개해 완료한 뒤 이 요청을 처리하세요.",
                json_output=args.json,
            )
            return 2
    else:
        if request.base_pinset_sha256 != registry.pinset_sha256:
            # 자동으로 지우지 않는다 — 무엇이 버려지는지 운영자가 보고 결정해야 한다.
            _print_pin_command_failure(
                "요청이 만들어진 이후 pin이 바뀌었습니다(요청 base "
                f"{request.base_pinset_sha256[:12]}... vs 현재 "
                f"{registry.pinset_sha256[:12]}...). UI에서 취소 후 다시 요청하거나 "
                f'"ktdctl pin clear-pending --request-id {request.request_id} --confirm"'
                "으로 지우세요.",
                json_output=args.json,
            )
            return 2

        expected = prospective_pinset_sha256(
            release_version=registry.release_version,
            map_revision=(
                request.revision if request.role == "map" else registry.map_revision
            ),
            pinvi_revision=(
                request.revision if request.role == "pinvi" else registry.pinvi_revision
            ),
        )
        if expected != request.prospective_pinset_sha256:
            _print_pin_command_failure(
                "request digest does not match the canonical recomputation",
                json_output=args.json,
            )
            return 2
        if registry.is_blocked_pinset(expected):
            _print_pin_command_failure(
                "this request targets a permanently blocked pinset", json_output=args.json
            )
            return 2
        if expected == registry.pinset_sha256:
            _print_pin_command_failure(
                "this request would not change any revision", json_output=args.json
            )
            return 2

    try:
        updated = _rotate_single_role_source(
            role=request.role,
            revision=request.revision,
            reason=_applied_reason(request),
            rotated_by=_applied_actor(request),
            block_previous=args.block_previous,
        )
    except DeploymentContractError as exc:
        _print_pin_command_failure(str(exc), json_output=args.json)
        print("(요청은 그대로 남아 있습니다)", file=sys.stderr)
        if "pair rotation" in str(exc):
            # 단일 role 요청으로는 해소되지 않는 상태다. 실제 해소 명령을 준다.
            print(
                "해소: ktdctl pin rotate-pair --map-revision <40-hex> "
                '--pinvi-revision <40-hex> --reason "<사유>" --confirm',
                file=sys.stderr,
            )
        return 2
    except OSError as exc:
        # v5/v6 transaction 도중의 I/O 실패다. 요청과 durable intent가 모두 남으므로
        # 같은 apply-pending 재실행이 미완 transaction을 재개하고 요청을 정리한다.
        _print_rotation_write_failure(exc, json_output=args.json)
        print("(요청은 그대로 남아 있습니다)", file=sys.stderr)
        return 2

    # 여기서부터 registry는 이미 바뀌었다. 남은 실패는 전부 "적용됐으나 정리 미완"이며
    # exit 3으로 구분한다 — 1(할 일 없음)과 같은 코드를 쓰면 스크립트가 "적용 안 됨"으로
    # 오해하고, terminal 규약 때문에 그 오해가 pinset 하나를 태운다.
    def _applied(message: str | None = None) -> int:
        if message:
            print(message, file=sys.stderr)
        if not args.json:
            print(
                f"applied pending rotation for {request.role}; "
                f"new pinset {updated.pinset_sha256}"
            )
        _print_registry(updated, json_output=args.json)
        return 3 if message else 0

    try:
        cleared = clear_runtime_pin_request(expect_request_id=request.request_id)
    except (OSError, DeploymentContractError) as exc:
        return _applied(
            "회전은 적용됐으나 요청 파일을 지우지 못했습니다 — 수동으로 삭제하세요: "
            f"{runtime_pin_request_path()} ({exc})"
        )
    if not cleared:
        return _applied(
            "회전은 적용됐지만 요청 파일의 id가 그 사이 달라져 지우지 않았습니다"
            "(취소되었거나 새 요청으로 교체됐습니다): "
            f"{runtime_pin_request_path()}"
        )
    return _applied()


def _cmd_pin_clear_pending(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("pin clear-pending requires --confirm (no file was written)", file=sys.stderr)
        return 2
    if args.force:
        # 읽을 수 없는 파일은 id를 알 수 없어 id 대조 삭제로는 영원히 지울 수 없고,
        # 파일이 있으니 새 요청도 받을 수 없다 — 회전 요청 경로 전체가 잠긴다.
        try:
            discarded = discard_unreadable_runtime_pin_request()
        except DeploymentContractError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if discarded is None:
            print("지울 손상된 요청 파일이 없습니다.", file=sys.stderr)
            return 1
        print(f"discarded an unreadable pending request file: {discarded}")
        return 0
    if not args.request_id:
        print(
            "pin clear-pending requires --request-id <id> (or --force for an unreadable file)",
            file=sys.stderr,
        )
        return 2
    try:
        cleared = clear_runtime_pin_request(expect_request_id=args.request_id)
    except DeploymentContractError as exc:
        print(str(exc), file=sys.stderr)
        print(
            "손상된 요청 파일은 '--force --confirm'으로 지웁니다.",
            file=sys.stderr,
        )
        return 2
    if not cleared:
        print("그 id의 대기 중인 요청이 없습니다.", file=sys.stderr)
        return 1
    print(f"discarded pending rotation request {args.request_id}")
    return 0


def _cmd_pin_rollback(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("pin rollback requires --confirm (no file was written)", file=sys.stderr)
        return 2
    try:
        with _runtime_pin_mutation_lock(allow_pending_pair_recovery=True):
            if load_pending_runtime_pair_rotation() is not None or _uses_execution_registry():
                # v6 host의 rollback은 execution과 함께 기록한다. v5만 앞서간 stale
                # 상태의 치유형 원복에서는 helper가 execution을 그대로 보존한다.
                registry = rollback_with_execution(
                    pinset_sha256=args.to,
                    manager_source_revision=trusted_manager_source_revision(),
                    reason=args.reason,
                    rotated_by=_pin_actor(),
                )
            else:
                registry = rollback_runtime_pin(
                    pinset_sha256=args.to,
                    rotated_by=_pin_actor(),
                    reason=args.reason,
                )
    except DeploymentContractError as exc:
        _print_pin_registry_failure(exc, json_output=args.json)
        return 2
    except OSError as exc:
        _print_rotation_write_failure(exc, json_output=args.json)
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

    pin_migrate_execution = pin_subparsers.add_parser(
        "migrate-execution-v6",
        help="v5 source pin을 보존하고 trusted Manager-aware execution registry를 생성합니다.",
    )
    pin_migrate_execution.add_argument("--reason", required=True)
    pin_migrate_execution.add_argument("--confirm", action="store_true")
    pin_migrate_execution.add_argument("--json", action="store_true")
    pin_migrate_execution.set_defaults(func=_cmd_pin_migrate_execution)

    pin_rebind_execution = pin_subparsers.add_parser(
        "rebind-execution",
        help="현재 execution을 새 trusted Manager release로 재결박합니다.",
    )
    pin_rebind_execution.add_argument("--expected-manager-revision", required=True)
    pin_rebind_execution.add_argument("--reason", required=True)
    pin_rebind_execution.add_argument("--confirm", action="store_true")
    pin_rebind_execution.add_argument("--json", action="store_true")
    pin_rebind_execution.set_defaults(func=_cmd_pin_rebind_execution)

    pin_show_execution = pin_subparsers.add_parser(
        "show-execution", help="현재 Manager-aware execution binding을 읽기 전용으로 출력합니다."
    )
    pin_show_execution.add_argument("--json", action="store_true")
    pin_show_execution.set_defaults(func=_cmd_pin_show_execution)

    pin_block_execution = pin_subparsers.add_parser(
        "block-execution", help="현재 trusted runtime execution을 terminal 처리합니다."
    )
    pin_block_execution.add_argument("--reason", required=True)
    pin_block_execution.add_argument(
        "--phase",
        default=None,
        help=(
            "실패 phase로 scoped 차단 기록을 남깁니다(보정 후 재실행 가능). "
            "생략하면 무조건 차단(terminal)입니다."
        ),
    )
    pin_block_execution.add_argument("--confirm", action="store_true")
    pin_block_execution.add_argument("--json", action="store_true")
    pin_block_execution.set_defaults(func=_cmd_pin_block_execution)

    pin_publish_generation = pin_subparsers.add_parser(
        "publish-generation",
        help="검증된 private manifest·journal을 API용 공개 사본으로 원자 복제합니다.",
    )
    pin_publish_generation.add_argument(
        "--manifest",
        required=True,
        help="root-owned pinned-runtime-generation-v6.json의 절대 경로입니다.",
    )
    pin_publish_generation.add_argument(
        "--journal",
        required=True,
        help="root-owned current pinned-runtime-rebuild-v8-<pinset>.json의 절대 경로입니다.",
    )
    pin_publish_generation.add_argument(
        "--confirm",
        action="store_true",
        help="비밀 없는 API 관측 사본 갱신을 확인합니다.",
    )
    pin_publish_generation.add_argument("--json", action="store_true")
    pin_publish_generation.set_defaults(func=_cmd_pin_publish_generation)

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

    pin_rotate_pair = pin_subparsers.add_parser(
        "rotate-pair",
        help="Map·PinVi revision을 intermediate pinset 없이 원자적으로 회전합니다.",
    )
    pin_rotate_pair.add_argument("--map-revision", required=True, help="Map 40-hex commit SHA입니다.")
    pin_rotate_pair.add_argument(
        "--pinvi-revision", required=True, help="PinVi 40-hex commit SHA입니다."
    )
    pin_rotate_pair.add_argument(
        "--reason",
        required=True,
        help="회전 사유(감사 기록 필수). world-readable 공개 사본에 그대로 기록되므로 비밀을 적지 않습니다.",
    )
    pin_rotate_pair.add_argument(
        "--block-previous",
        action="store_true",
        help="직전 pinset을 terminal로 등재해 재시도를 영구 차단합니다.",
    )
    pin_rotate_pair.add_argument("--confirm", action="store_true")
    pin_rotate_pair.add_argument("--json", action="store_true")
    pin_rotate_pair.set_defaults(func=_cmd_pin_rotate_pair)

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

    pin_apply = pin_subparsers.add_parser(
        "apply-pending", help="UI가 기록한 회전 요청을 root 권한으로 적용합니다."
    )
    pin_apply.add_argument(
        "--expect-revision", help="이 revision을 가리키는 요청이 아니면 적용하지 않습니다."
    )
    pin_apply.add_argument(
        "--any-revision",
        action="store_true",
        help="대기 중인 요청의 revision을 확인하지 않고 그대로 적용합니다.",
    )
    pin_apply.add_argument(
        "--block-previous",
        action="store_true",
        help="직전 pinset을 terminal로 등재해 재시도를 영구 차단합니다.",
    )
    pin_apply.add_argument("--confirm", action="store_true")
    pin_apply.add_argument("--json", action="store_true")
    pin_apply.set_defaults(func=_cmd_pin_apply_pending)

    pin_show_pending = pin_subparsers.add_parser(
        "show-pending", help="대기 중인 회전 요청을 출력합니다(읽기 전용)."
    )
    pin_show_pending.add_argument("--json", action="store_true")
    pin_show_pending.set_defaults(func=_cmd_pin_show_pending)

    pin_clear_pending = pin_subparsers.add_parser(
        "clear-pending", help="대기 중인 회전 요청을 적용하지 않고 폐기합니다."
    )
    pin_clear_pending.add_argument("--request-id", help="폐기할 요청 id입니다.")
    pin_clear_pending.add_argument(
        "--force",
        action="store_true",
        help="읽을 수 없는 요청 파일을 파싱하지 않고 지웁니다(id 불필요).",
    )
    pin_clear_pending.add_argument("--confirm", action="store_true")
    pin_clear_pending.set_defaults(func=_cmd_pin_clear_pending)

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

    db_backup_restore_plan = db_backup_subparsers.add_parser(
        "restore-plan",
        help=(
            "이 백업으로 복원하면 무슨 일이 일어나는지 계산합니다(읽기 전용). "
            "실제 role DB로 덮어쓰는 복원 명령은 아직 없습니다(scratch DB 검증은 "
            "rehearse-restore)."
        ),
    )
    db_backup_restore_plan.add_argument("role", choices=BACKUP_ROLES)
    db_backup_restore_plan.add_argument(
        "--file", help="검사할 백업 파일명. 생략하면 가장 최근 백업입니다."
    )
    db_backup_restore_plan.add_argument("--json", action="store_true")
    db_backup_restore_plan.set_defaults(func=_cmd_db_backup_restore_plan)

    db_backup_rehearse_restore = db_backup_subparsers.add_parser(
        "rehearse-restore",
        help=(
            "백업을 같은 인스턴스의 scratch DB에 실제로 복원해 검증합니다. 운영 DB는 "
            "전혀 건드리지 않고 검증 후 scratch DB를 지웁니다. 실제 role DB로 "
            "덮어쓰는 파괴적 복원은 아직 없습니다."
        ),
    )
    db_backup_rehearse_restore.add_argument("role", choices=BACKUP_ROLES)
    db_backup_rehearse_restore.add_argument(
        "--file", help="검사할 백업 파일명. 생략하면 가장 최근 백업입니다."
    )
    db_backup_rehearse_restore.add_argument(
        "--timeout",
        type=int,
        default=14_400,
        help="pg_restore 대기 한도(초). 큰 인스턴스는 넉넉히 늘리세요(기본 4시간).",
    )
    db_backup_rehearse_restore.add_argument("--json", action="store_true")
    db_backup_rehearse_restore.set_defaults(func=_cmd_db_backup_rehearse_restore)

    offbox_sync = subparsers.add_parser(
        "offbox-sync",
        help=(
            "백업과 pin registry 보존본을 설정된 원격 호스트로 옮기고 sha256sum -c로 "
            "재검증합니다(KTDM_OFFBOX_HOST 등 env 필요)."
        ),
    )
    offbox_sync_subparsers = offbox_sync.add_subparsers(
        dest="offbox_sync_action", required=True
    )

    offbox_sync_run = offbox_sync_subparsers.add_parser(
        "run", help="지금 모든 role과 pin registry 보존본을 동기화합니다."
    )
    offbox_sync_run.add_argument(
        "--skip-pin-registry",
        action="store_true",
        help="pin registry 보존본 동기화를 건너뜁니다(백업만).",
    )
    offbox_sync_run.add_argument(
        "--timeout",
        type=int,
        default=14_400,
        help="role별 rsync 대기 한도(초, 기본 4시간).",
    )
    offbox_sync_run.add_argument("--json", action="store_true")
    offbox_sync_run.set_defaults(func=_cmd_offbox_sync_run)

    offbox_sync_status = offbox_sync_subparsers.add_parser(
        "status", help="마지막 동기화 결과를 읽기 전용으로 출력합니다."
    )
    offbox_sync_status.add_argument("--json", action="store_true")
    offbox_sync_status.set_defaults(func=_cmd_offbox_sync_status)

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
