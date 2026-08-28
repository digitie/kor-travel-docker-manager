"""재구축을 **지금 시작할 수 있는가**를 읽기 전용으로 판정한다 (KUM-M14 / 설계 Q5).

설계는 "rehearsal 한정 재구축 버튼"을 적었지만 그것은 만들 수 없다.
``pinvi-pair rebuild-pinned``는 root를 요구하고(`_require_pinned_runtime_rebuild_root`),
backend가 root로 도는 호스트에서도 HTTP 요청 하나가 3개 DB를 날리는 파괴적 작업을
시작할 수 있게 만드는 것은 **경계를 없애는 것**이지 편의가 아니다.

그래서 화면이 하는 일을 둘로 나눈다. **판정은 여기서** 하고(그것이 값싸고 안전하다),
**실행은 SSH**에 남긴다. 이 모듈은 어떤 mutation도 하지 않고 어떤 명령도 실행하지
않는다 — 이미 존재하는 읽기 전용 관측 셋을 합쳐 "지금 누르면 되는가"에 답할 뿐이다.

**왜 이것이 버튼보다 나은가**: 비전문 관리자에게 실제 장벽은 "SSH로 가라"가 아니라
"가서 무엇을 쳐야 하고, 지금 쳐도 되는지"다. 차단 사유를 사람 말로 보여 주고 정확한
명령을 복사하게 하면 그 장벽이 사라지면서도 파괴적 실행의 마찰은 그대로 남는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from kor_travel_docker_manager.services.admin_password_service import (
    pinned_rebuild_guard_state,
)
from kor_travel_docker_manager.services.deployment_readiness import (
    read_deployment_readiness,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    read_published_pinned_runtime_generation,
)
from kor_travel_docker_manager.services.runtime_pin_registry import (
    read_published_runtime_pins,
)

PINNED_REBUILD_PREFLIGHT_SCHEMA: Final = "ktdm.pinned-rebuild-preflight.v1"
REBUILD_COMMAND: Final = "sudo -n backend/.venv/bin/ktdctl pinvi-pair rebuild-pinned --confirm"
PIN_VERIFY_COMMAND: Final = "sudo -n backend/.venv/bin/ktdctl pin verify"
PIN_SHOW_COMMAND: Final = "sudo -n backend/.venv/bin/ktdctl pin show"


def _now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _finding(code: str, text: str, next_action: str = "") -> dict[str, str]:
    return {"code": code, "text": text, "next_action": next_action}


def read_pinned_rebuild_preflight(*, force_refresh: bool = False) -> dict[str, Any]:
    """재구축 실행 가능 여부. **절대 예외를 던지지 않는다.**

    판정 근거를 하나라도 잃으면 ``unverified``로 떨어뜨린다 — 근거 없이 초록불을 켜면
    사람이 pinset 하나를 태우고, terminal 규약 때문에 그것은 되돌릴 수 없다.
    """

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    unverified: list[dict[str, str]] = []

    # 1. 고정 값을 신뢰할 수 있는가. 신뢰할 수 없으면 어느 pinset을 재구축하는지조차
    #    말할 수 없으므로 나머지 판정도 의미가 없다.
    try:
        published = read_published_runtime_pins()
    except Exception as exc:  # noqa: BLE001 - 진단 route는 500이 되면 안 된다
        published = {"status": "unknown", "detail": str(exc)}
    pin_status = published.get("status")
    pinset_sha256 = published.get("pinset_sha256")
    if pin_status != "ok":
        unverified.append(
            _finding(
                "PINS_UNVERIFIED",
                f"고정된 버전이 권위 있는 값으로 확인되지 않았습니다(status={pin_status}).",
                PIN_VERIFY_COMMAND,
            )
        )
    else:
        # phase 없는 차단만 시작 게이트가 본다. phase 한정 차단은 journal 재개만 막으므로
        # 여기서 합치면 재구축이 실제로 허용되는데도 "회전하라"고 말하게 된다.
        blocked = [
            entry
            for entry in published.get("blocked_pinsets", [])
            if isinstance(entry, dict)
            and entry.get("pinset_sha256") == pinset_sha256
            and entry.get("phase") is None
        ]
        if blocked:
            blockers.append(
                _finding(
                    "PINSET_TERMINAL",
                    "지금 고정된 버전 세트는 재시도가 영구 금지된 조합입니다. 새 "
                    "revision으로 회전해야 재구축할 수 있습니다.",
                    PIN_SHOW_COMMAND,
                )
            )

    # 2. v6/v8 공개 세대가 현재 registry의 one-shot 계약과 정합한가. registry만
    #    green이면 stale/partial generation을 무시하고 destructive command를 안내할 수
    #    있다. rotation 직후의 strict old committed/unconditional-terminal generation은
    #    `pending_rebuild`로 유효하지만, 그 외 partial·drift·unknown은 fail-close다.
    try:
        generation = read_published_pinned_runtime_generation()
    except Exception as exc:  # noqa: BLE001 - 진단 route는 500이 되면 안 된다
        generation = {"status": "unknown", "detail": str(exc)}
    generation_status = generation.get("status")
    generation_binding = generation.get("pinset_binding")
    binding_status = (
        generation_binding.get("status")
        if isinstance(generation_binding, dict)
        else None
    )
    if generation_status != "ok" or binding_status not in {"match", "pending_rebuild"}:
        unverified.append(
            _finding(
                "GENERATION_UNVERIFIED",
                "공개된 runtime generation이 현재 one-shot 계약과 정합한지 확인하지 "
                f"못했습니다(status={generation_status}, binding={binding_status}).",
                PIN_VERIFY_COMMAND,
            )
        )

    # 3. 배포 모드가 재구축을 허용하는가, 그리고 진행 중인 재구축이 있는가.
    #    두 사실이 같은 판정 함수에서 나온다 — 모드 게이트를 통과해야 journal도 읽는다.
    try:
        guard = pinned_rebuild_guard_state()
    except Exception as exc:  # noqa: BLE001
        guard = {"verdict": "unknown", "detail": str(exc)}
    verdict = guard.get("verdict")
    if verdict == "not_rebuildable":
        blockers.append(
            _finding(
                "MODE_NOT_REBUILDABLE",
                "이 배포 모드에서는 재구축을 시작할 수 없습니다. 운영 환경은 일반 "
                "runtime mutation을 차단합니다.",
            )
        )
    elif verdict == "unfinished_journal":
        # 차단이 아니다 — 이 상태에서 rebuild-pinned는 **재개**한다. 다만 새로 시작하는
        # 것이 아니라는 사실을 모르면 결과를 잘못 읽는다.
        warnings.append(
            _finding(
                "JOURNAL_WILL_RESUME",
                f"진행 중인 재구축 기록이 있습니다. 지금 실행하면 새로 시작하지 않고 "
                f"그 지점부터 재개합니다. {guard.get('detail', '')}".strip(),
            )
        )
    elif verdict in {"unverifiable", "unknown"}:
        unverified.append(
            _finding(
                "JOURNAL_UNVERIFIABLE",
                f"진행 중인 재구축이 있는지 확인할 수 없습니다. {guard.get('detail', '')}".strip(),
            )
        )

    # 4. 실행 전에 알 수 있는 결손(사전 점검).
    try:
        # 같은 화면의 사전 점검 섹션과 다른 스냅샷을 보면 두 판정이 서로 모순되게 보인다.
        readiness = read_deployment_readiness(force_refresh=force_refresh)
    except Exception as exc:  # noqa: BLE001
        readiness = {"summary": {"state": "unverified", "text": str(exc)}, "checks": []}
    readiness_summary = readiness.get("summary", {})
    readiness_state = readiness_summary.get("state")
    if readiness_state == "blocked":
        for check in readiness.get("checks", []):
            if isinstance(check, dict) and check.get("state") == "missing":
                blockers.append(
                    _finding(
                        f"READINESS_{str(check.get('id', 'unknown')).upper()}",
                        f"{check.get('label_ko', '')}: {check.get('detail', '')}".strip(),
                    )
                )
    elif readiness_state == "unverified":
        unverified.append(
            _finding(
                "READINESS_UNVERIFIED",
                str(readiness_summary.get("text", "사전 점검을 완료하지 못했습니다.")),
            )
        )

    if blockers:
        state = "blocked"
        text = "지금 재구축을 실행하면 실패하거나 거부됩니다."
    elif unverified:
        state = "unverified"
        text = (
            "재구축을 실행해도 되는지 확인하지 못했습니다. 화면 값만으로 판단하지 마세요."
        )
    elif warnings:
        # 차단은 아니지만 초록불도 아니다. "막는 요인이 없다 + 실행하세요"를 띄우면
        # 운영자는 새로 시작한다고 믿고 누르고, 실제로는 중단됐던 재구축이 재개된다.
        # 그 오해가 정확히 pinset 하나와 반나절을 태우는 경로다.
        state = "attention"
        text = (
            "재구축을 막는 요인은 없지만, 그냥 시작되지 않습니다. 아래 내용을 먼저 "
            "읽으세요."
        )
    else:
        state = "ok"
        text = (
            "재구축을 막는 요인을 찾지 못했습니다(성공을 보장하지는 않습니다). 아래 "
            "명령을 SSH에서 실행하세요."
        )

    return {
        "schema": PINNED_REBUILD_PREFLIGHT_SCHEMA,
        "collected_at": _now(),
        # 이 값이 true여도 화면은 실행하지 않는다. 실행 주체는 언제나 SSH의 사람이다.
        # `attention`은 실행 가능하지만 **읽고 나서** 해야 하는 상태다.
        "can_start": state in {"ok", "attention"},
        "pinset_sha256": pinset_sha256 if pin_status == "ok" else None,
        "blockers": blockers,
        "warnings": warnings,
        "unverified": unverified,
        "command": REBUILD_COMMAND,
        "summary": {"state": state, "text": text},
    }


__all__ = [
    "PINNED_REBUILD_PREFLIGHT_SCHEMA",
    "REBUILD_COMMAND",
    "read_pinned_rebuild_preflight",
]
