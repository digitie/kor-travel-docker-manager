"""수집 감시의 code-server 자동 재시작(소유자 승인 2026-11-24 — 제한적).

n150 PinVi(11-13~16)·concierge(11-14~18) 사건은 code-server 안 `dagster api grpc` 프로세스가 fd(soft 1024)를 다 써서 55·100시간
동안 모든 launch와 sensor가 실패했고, 사람이 재시작할 때까지 풀리지 않았다. 감시는 그 상태를 잡지만(`dagster_fd_usage`·
`launch_failure_streak`) 그동안 재시작은 사람 몫이었다. 소유자는 다음 조건에서만 자동 재시작을 허용했다.

- 계기: 그 code-server 컨테이너의 grpc 프로세스 fd 비율 ≥ ``fd_ratio``(기본 0.9), 또는 그 location의 run launch 연속 실패
  (`launch_failure_streak` 경보 — 횟수는 그 규칙의 ``count``).
- 그 location에 **실행 중 run이 0**이다(STARTING·STARTED·CANCELING — code-server 안에서 도는 run을 끊지 않는다).
- 같은 컨테이너는 ``min_interval_hours``(기본 6시간)에 한 번 — 시도 시각을 먼저 기록하므로 실패도 한 번으로 센다(재시작 폭주 없음).
- 이 호스트가 ``hosts``에 있다(기본 n150).
- 재시작은 Manager의 정식 컨테이너 수명주기 경로(`docker_service.control_container(…, "restart")`, 화면의 재시작 단추와 같은 길)로만
  한다 — Manager compose 컨테이너면 C6c host lock(비대기 — 배포가 돌고 있으면 아무것도 바꾸지 않고 거부)과 Manager mutation 환경
  계약을 그대로 지난다. registry가 관리하는 code-server 컨테이너만 대상이다.
- 재시작 전후 그 컨테이너의 fd 내역 진단(`capture_fds`)을 남기고, Telegram으로 결과를 보낸다.

이 모듈은 판정(순수 함수)과 상태 파일만 갖는다. 실행 순서는 `plane_watchdog.PlaneWatchdog._maybe_auto_restart`가 정한다.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RUNNING_STATUSES = ("STARTING", "STARTED", "CANCELING")


@dataclass(frozen=True)
class RestartCandidate:
    container: str
    location: str
    rule: str  # 계기가 된 경보 규칙
    reason: str


def restart_candidates(
    findings: Sequence[Any],
    containers_by_location: Mapping[str, str],
    settings: Mapping[str, Any],
) -> tuple[list[RestartCandidate], list[str]]:
    """경보에서 재시작 후보를 고른다. 두 번째 값은 후보가 되지 못한 이유(화면용)."""

    threshold = float(settings["fd_ratio"])
    candidates: dict[str, RestartCandidate] = {}
    skipped: list[str] = []
    location_by_container = {container: location for location, container in containers_by_location.items()}
    for finding in findings:
        evidence = finding.evidence
        if finding.rule == "dagster_fd_usage":
            ratio = float(evidence.get("ratio") or 0)
            if evidence.get("process_kind") != "grpc" or ratio < threshold:
                continue
            container = finding.subject
            location = location_by_container.get(container, "")
            reason = (f"grpc 프로세스(pid {evidence.get('pid')}) 열린 fd {evidence.get('open_fds')}/"
                      f"{evidence.get('nofile_soft_limit')}({ratio:.0%}) ≥ {threshold:.0%}")
        elif finding.rule == "launch_failure_streak" and settings["on_launch_failure_streak"]:
            location = finding.subject
            container = containers_by_location.get(location, "")
            if not container:
                skipped.append(f"{location}: launch 연속 실패지만 그 location의 code-server 컨테이너를 모릅니다")
                continue
            reason = f"run launch {evidence.get('streak')}회 연속 실패({evidence.get('first_failure_at')}부터)"
        else:
            continue
        if not location:
            skipped.append(f"{container}: 컨테이너의 location을 모릅니다(실행 중 run을 확인할 수 없어 재시작하지 않습니다)")
            continue
        candidates.setdefault(container, RestartCandidate(container, location, finding.rule, reason))
    return list(candidates.values()), skipped


class RestartLedger:
    """컨테이너별 마지막 자동 재시작 **시도** 시각. 파일(0600)에 두어 Manager 재시작에도 6시간 한도를 지킨다."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._attempts: dict[str, float] = {}
        if path is not None:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if isinstance(data, dict):
                self._attempts = {str(key): float(value) for key, value in data.items()
                                  if isinstance(value, (int, float)) and not isinstance(value, bool)}

    def last_attempt(self, container: str) -> float | None:
        return self._attempts.get(container)

    def record(self, container: str, at: float) -> bool:
        """시도 시각을 남긴다. 파일에 쓰지 못하면 False — 그때는 재시작하지 않는다(한도를 지킬 수 없다)."""

        self._attempts[container] = at
        if self._path is None:
            return True
        try:
            self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._attempts), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
        except OSError as exc:
            logger.error("auto-restart ledger not saved: %s", exc.strerror or type(exc).__name__)
            return False
        return True


def format_restart_message(record: Mapping[str, Any], host: str, link: str | None) -> str:
    ok = bool(record.get("success"))
    lines = [
        f"[자동 조치] code-server 재시작 {'완료' if ok else '실패'} · {host or '?'}",
        f"컨테이너: {record.get('container')}",
        f"location: {record.get('location')}",
        f"계기: {record.get('rule')} — {record.get('reason')}",
        "실행 중 run 0을 확인한 뒤 재시작했습니다." if ok else f"오류: {record.get('error')}",
    ]
    for phase in ("before", "after"):
        directory = (record.get("diagnostics") or {}).get(phase)
        if directory:
            lines.append(f"진단({'전' if phase == 'before' else '후'}): {directory}")
    if link:
        lines.append(f"대시보드: {link}")
    return "\n".join(lines)


__all__ = ["RUNNING_STATUSES", "RestartCandidate", "RestartLedger", "format_restart_message", "restart_candidates"]
