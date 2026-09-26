"""재구축을 **지금 시작할 수 있는가**를 읽기 전용으로 판정한다 (KUM-M14 / 설계 Q5).

설계는 "rehearsal 한정 재구축 버튼"을 적었지만 그것은 만들 수 없다.
``pinvi-pair rebuild-pinned``는 root를 요구하고(`_require_pinned_runtime_rebuild_root`),
backend가 root로 도는 호스트에서도 HTTP 요청 하나가 고정 pair 전체를 다시 배포하는
작업(``--restart``면 3개 DB까지 지운다)을 시작할 수 있게 만드는 것은 **경계를 없애는
것**이지 편의가 아니다.

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

from kor_travel_docker_manager.services.deployment_readiness import (
    read_deployment_mode,
    read_deployment_readiness,
)
from kor_travel_docker_manager.services.runtime_pin_registry import (
    read_published_runtime_pins,
)

PINNED_REBUILD_PREFLIGHT_SCHEMA: Final = "ktdm.pinned-rebuild-preflight.v1"
REBUILD_COMMAND: Final = "sudo -n backend/.venv/bin/ktdctl pinvi-pair rebuild-pinned --confirm"
PIN_VERIFY_COMMAND: Final = "sudo -n backend/.venv/bin/ktdctl pin verify"


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
        # phase 없는 차단만 본다. phase가 있는 항목은 옛 journal의 그 단계 재개만 막던
        # 기록이고, 재개는 ADR-51에서 없어졌다. 조건 없는 차단도 재구축을 막지 않고
        # 경고로만 남지만(ADR-51), 그 기록이 있으면 아래처럼 root 검증을 요구한다.
        blocked = [
            entry
            for entry in published.get("blocked_pinsets", [])
            if isinstance(entry, dict)
            and entry.get("pinset_sha256") == pinset_sha256
            and entry.get("phase") is None
        ]
        if blocked:
            # v5 terminal에는 Manager revision이 없다. 비-root UI가 public source
            # audit만으로 새 v6 execution의 실행권을 판정하면 거짓 초록불이 된다.
            # root ``pin verify``만 trusted install provenance와 private execution
            # registry를 함께 확인할 수 있으므로, 회전을 강요하지 않고 fail-close한다.
            unverified.append(
                _finding(
                    "LEGACY_SOURCE_TERMINAL",
                    "현재 고정된 source 버전 세트에 legacy terminal 기록이 있습니다. "
                    "새 trusted execution의 실행 가능 여부를 root 검증으로 확인해야 합니다.",
                    PIN_VERIFY_COMMAND,
                )
            )
        else:
            # execution registry의 authoritative copy는 root 0600이며 public copy는
            # private copy와의 parity를 비-root UI가 증명할 수 없다. source가 깨끗해도
            # v6 terminal을 보지 못한 채 초록불을 주면 one-shot을 재실행하게 된다.
            unverified.append(
                _finding(
                    "EXECUTION_VERIFICATION_REQUIRED",
                    "현재 trusted runtime execution의 terminal 상태는 root 검증으로만 "
                    "확인할 수 있습니다.",
                    PIN_VERIFY_COMMAND,
                )
            )

    # 2. (없어졌다, ADR-51 D-1) v6 공개 세대 대조는 지웠다. 배포가 끝났는지는 root-only
    #    `deploy-status.json`만 알고, 그 공개 사본은 두지 않는다 — §1이 이미 항상
    #    root 검증을 요구하므로 판정은 바뀌지 않는다.

    # 3. 배포 모드가 재구축을 허용하는가. rehearsal/rebuildable이 아니면 rebuild-pinned는
    #    시작하자마자 거부한다. `.env`나 모드를 읽지 못하면 추측하지 않는다.
    try:
        mode = read_deployment_mode()
    except Exception as exc:  # noqa: BLE001 - 진단 route는 500이 되면 안 된다
        unverified.append(
            _finding("MODE_UNVERIFIABLE", f"배포 모드를 확인하지 못했습니다: {exc}")
        )
    else:
        if not mode.rebuildable:
            blockers.append(
                _finding(
                    "MODE_NOT_REBUILDABLE",
                    "이 배포 모드에서는 재구축을 시작할 수 없습니다. 운영 환경은 일반 "
                    "runtime mutation을 차단합니다.",
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
        "can_start": state == "ok",
        "pinset_sha256": pinset_sha256 if pin_status == "ok" else None,
        "blockers": blockers,
        "unverified": unverified,
        "command": REBUILD_COMMAND,
        "summary": {"state": state, "text": text},
    }


__all__ = [
    "PINNED_REBUILD_PREFLIGHT_SCHEMA",
    "REBUILD_COMMAND",
    "read_pinned_rebuild_preflight",
]
