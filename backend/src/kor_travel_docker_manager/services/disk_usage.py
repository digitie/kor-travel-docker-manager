"""Docker 디스크 사용량 관측 — "정리하면 얼마나 확보되나"를 사람 말로 답한다.

설계 정본: ``docs/ktdctl-ui-migration.md`` P8b.

비전문 관리자가 이 시스템을 죽이는 가장 그럴듯한 경로가 "디스크 참"이다. 그런데
현재 어느 화면도 그것을 보여 주지 않고, 알려면 SSH에서 ``docker system df``를 쳐야
한다. 이 모듈은 그 값을 읽어 **원시 수치가 아니라 "정리 시 약 N GB 확보 가능"**으로
옮긴다 — 운영자가 알아야 하는 것은 바이트 수가 아니라 "지금 뭘 해야 하는가"다.

관측만 한다. ``docker system prune``은 절대 부르지 않는다 — 이 카드는 정리 여부를
사람이 판단하도록 정보를 줄 뿐이고, 실제 정리는 CLI 전용으로 남는다.
"""

from __future__ import annotations

import copy
import json
import subprocess
import threading
import time
from typing import Any, Final

from kor_travel_docker_manager.services.runtime_pin_registry import utc_timestamp

DISK_USAGE_SCHEMA: Final = "kor-travel-docker-manager.disk-usage.v1"
CACHE_TTL_SECONDS: Final = 60.0
_DOCKER_TIMEOUT_SECONDS: Final = 20.0
_PROBE_LOCK_WAIT_SECONDS: Final = 5.0
# 85%를 넘으면 build/pull이 실패하기 시작한다. 그 전에 알려 주는 것이 이 카드의 목적이다.
_WARN_RECLAIMABLE_BYTES: Final = 20 * 1024**3

_TYPE_LABELS: Final[dict[str, str]] = {
    "Images": "이미지",
    "Containers": "컨테이너",
    "Local Volumes": "볼륨",
    "Build Cache": "빌드 캐시",
}


def _format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    size = float(value)
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} PB"


def _parse_size(raw: object) -> int | None:
    """``docker system df --format json``의 사람용 크기 문자열을 바이트로 바꾼다.

    docker는 여기서 ``1.234GB``처럼 접미사가 붙은 문자열을 준다. 파싱에 실패하면
    추측하지 않고 ``None``이다 — 잘못된 숫자는 없는 숫자보다 나쁘다.
    """

    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text in {"N/A", "-"}:
        return None
    units = (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("kB", 1024), ("KB", 1024), ("B", 1))
    for suffix, multiplier in units:
        if text.endswith(suffix):
            number = text[: -len(suffix)].strip()
            try:
                return int(float(number) * multiplier)
            except ValueError:
                return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _run_docker_df() -> list[dict[str, Any]] | None:
    """``docker system df``를 읽기 전용으로 부른다. 실패는 예외가 아니라 ``None``이다."""

    try:
        completed = subprocess.run(
            ["docker", "system", "df", "--format", "{{json .}}"],
            cwd="/",
            capture_output=True,
            text=True,
            check=False,
            timeout=_DOCKER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            # 한 줄이 깨졌다고 전체를 버리지는 않되, 그 줄은 세지 않는다.
            continue
        if isinstance(document, dict):
            rows.append(document)
    return rows or None


def _unknown_payload(detail: str) -> dict[str, Any]:
    return {
        "schema": DISK_USAGE_SCHEMA,
        "collected_at": utc_timestamp(),
        "cached": False,
        "state": "unknown",
        "rows": [],
        "reclaimable_bytes": None,
        "summary": {
            "state": "unknown",
            "text": "디스크 사용량을 확인할 수 없습니다.",
            "detail": detail,
            "next_action": "sudo -n docker system df",
        },
    }


def _probe_disk_usage() -> dict[str, Any]:
    raw_rows = _run_docker_df()
    if raw_rows is None:
        return _unknown_payload("Docker daemon에 접근할 수 없거나 조회에 실패했습니다.")

    rows: list[dict[str, Any]] = []
    reclaimable_total = 0
    any_size_known = False
    for document in raw_rows:
        kind = document.get("Type")
        if not isinstance(kind, str):
            continue
        size = _parse_size(document.get("Size"))
        # Reclaimable은 "1.2GB (45%)" 형태라 괄호 앞부분만 본다.
        reclaimable_raw = document.get("Reclaimable")
        reclaimable = _parse_size(
            reclaimable_raw.split("(")[0] if isinstance(reclaimable_raw, str) else None
        )
        if size is not None:
            any_size_known = True
        if reclaimable is not None:
            reclaimable_total += reclaimable
        rows.append(
            {
                "type": kind,
                "label_ko": _TYPE_LABELS.get(kind, kind),
                "total_count": document.get("TotalCount"),
                "active_count": document.get("Active"),
                "size_bytes": size,
                "size_text": _format_bytes(size) if size is not None else None,
                "reclaimable_bytes": reclaimable,
                "reclaimable_text": (
                    _format_bytes(reclaimable) if reclaimable is not None else None
                ),
            }
        )

    if not rows or not any_size_known:
        return _unknown_payload("docker가 사용량을 알려 주지 않았습니다.")

    warn = reclaimable_total >= _WARN_RECLAIMABLE_BYTES
    return {
        "schema": DISK_USAGE_SCHEMA,
        "collected_at": utc_timestamp(),
        "cached": False,
        "state": "warn" if warn else "ok",
        "rows": rows,
        "reclaimable_bytes": reclaimable_total,
        "summary": {
            "state": "warn" if warn else "ok",
            # 원시 수치보다 "정리하면 얼마나 확보되나"가 운영자가 쓰는 정보다.
            "text": f"정리 시 약 {_format_bytes(reclaimable_total)} 확보 가능",
            "detail": (
                "회수 가능한 용량이 큽니다. 디스크가 차면 이미지 빌드와 재구축이 실패합니다."
                if warn
                else "지금은 여유가 있습니다."
            ),
            # 정리는 파괴적이므로 화면에서 실행하지 않고 명령만 알려 준다.
            "next_action": "sudo -n docker system prune --all --volumes" if warn else "",
        },
    }


_cache: tuple[float, dict[str, Any]] | None = None
_lock = threading.Lock()


def clear_disk_usage_cache() -> None:
    global _cache
    _cache = None


def read_disk_usage(*, force_refresh: bool = False) -> dict[str, Any]:
    """TTL 캐시 + 유한 대기 single-flight. **절대 예외를 던지지 않는다.**"""

    global _cache
    now = time.monotonic()
    cached = _cache
    if cached is not None and not force_refresh and now - cached[0] < CACHE_TTL_SECONDS:
        return copy.deepcopy(cached[1]) | {"cached": True}
    if not _lock.acquire(timeout=_PROBE_LOCK_WAIT_SECONDS):
        stale = _cache
        if stale is not None:
            return copy.deepcopy(stale[1]) | {"cached": True, "stale": True}
        return _unknown_payload("사용량 조회가 이미 실행 중입니다. 잠시 후 다시 조회하세요.")
    try:
        cached = _cache
        now = time.monotonic()
        if cached is not None and not force_refresh and now - cached[0] < CACHE_TTL_SECONDS:
            return copy.deepcopy(cached[1]) | {"cached": True}
        try:
            payload = _probe_disk_usage()
        except Exception as exc:  # noqa: BLE001 - 관측 카드는 500이 되면 안 된다
            return _unknown_payload(f"사용량을 수집하지 못했습니다: {exc}")
        _cache = (time.monotonic(), payload)
        return copy.deepcopy(payload) | {"cached": False}
    finally:
        _lock.release()
