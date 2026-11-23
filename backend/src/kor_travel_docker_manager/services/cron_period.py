"""cron 식에서 "실행 간격"만 유도한다 — 다음 실행 시각 계산기가 아니다.

수집 감시(`plane_watchdog`)는 "k주기 동안 성공이 없다"와 "N분간 tick이 없다"를 판정할 때
주기의 길이만 필요하다. 시각대(Dagster schedule의 `execution_timezone`)는 저장소의
instigator 행에 없으므로, 시각대와 무관한 값인 **연속 실행 사이 간격의 최소·최대**만 낸다.
한국 시각대에는 일광 절약 시간이 없어 간격은 시각대와 무관하다.

지원하는 식은 Dagster(croniter)가 받는 5필드 표준식과 `@hourly` 등 별칭이다. 해석할 수 없는
식은 `None`이다 — 잘못된 주기로 경보를 내는 것보다 그 규칙을 건너뛰는 편이 낫다.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections.abc import Iterable
from dataclasses import dataclass

_ALIASES = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}
_MONTH_NAMES = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_DOW_NAMES = {name: index for index, name in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}

#: 첫 실행을 찾는 기준 시각. 1월 1일에서 시작하면 지평 안에 길이가 다른 달이 모두 든다.
_REFERENCE = _dt.datetime(2026, 1, 1)
#: 연 1회 미만 식(2월 29일 등)은 간격을 정할 수 없다 — 지평 안에서 실행이 둘 미만이면 None이다.
_HORIZON_DAYS = 400
#: 촘촘한 식(`* * * * *`)은 이만큼의 실행이면 간격이 결정된다.
_MAX_FIRES = 2000


@dataclass(frozen=True)
class CronPeriod:
    min_gap_seconds: float
    max_gap_seconds: float


def _parse_value(token: str, names: dict[str, int]) -> int:
    lowered = token.lower()
    if lowered in names:
        return names[lowered]
    if not token.isdigit():
        raise ValueError(token)
    return int(token)


def _parse_field(field: str, low: int, high: int, names: dict[str, int] | None = None) -> set[int]:
    names = names or {}
    values: set[int] = set()
    for part in field.split(","):
        if not part:
            raise ValueError(field)
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) <= 0:
                raise ValueError(part)
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, _, last = base.partition("-")
            start, end = _parse_value(first, names), _parse_value(last, names)
        else:
            start = _parse_value(base, names)
            # `5/15`는 5부터 끝까지 15 간격이다(croniter와 같다).
            end = high if step_text else start
        if start < low or end > high or start > end:
            raise ValueError(part)
        values.update(range(start, end + 1, step))
    return values


def _fires(expression: str) -> Iterable[_dt.datetime]:
    text = _ALIASES.get(expression.strip().lower(), expression.strip())
    fields = text.split()
    if len(fields) != 5:
        raise ValueError(expression)
    minutes = sorted(_parse_field(fields[0], 0, 59))
    hours = sorted(_parse_field(fields[1], 0, 23))
    days = _parse_field(fields[2], 1, 31)
    months = _parse_field(fields[3], 1, 12, _MONTH_NAMES)
    weekdays = {value % 7 for value in _parse_field(fields[4], 0, 7, _DOW_NAMES)}
    day_restricted = fields[2] != "*"
    weekday_restricted = fields[4] != "*"
    for offset in range(_HORIZON_DAYS):
        day = _REFERENCE + _dt.timedelta(days=offset)
        if day.month not in months:
            continue
        by_day = day.day in days
        by_weekday = (day.isoweekday() % 7) in weekdays
        if day_restricted and weekday_restricted:
            # 표준 cron(croniter 기본값)은 둘 다 제한되면 OR이다.
            matched = by_day or by_weekday
        elif day_restricted:
            matched = by_day
        elif weekday_restricted:
            matched = by_weekday
        else:
            matched = True
        if not matched:
            continue
        for hour in hours:
            for minute in minutes:
                yield day.replace(hour=hour, minute=minute)


def _expressions(raw: str) -> list[str]:
    """Dagster는 cron 문자열 하나 또는 문자열 목록을 저장한다(목록은 JSON 문자열로 온다)."""

    text = raw.strip()
    if text.startswith("["):
        decoded = json.loads(text)
        if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
            raise ValueError(raw)
        return decoded
    return [text]


def cron_period(raw: str | None) -> CronPeriod | None:
    """연속 실행 간격의 최소·최대(초). 해석할 수 없거나 실행이 둘 미만이면 ``None``."""

    if not raw or not isinstance(raw, str):
        return None
    try:
        moments: set[_dt.datetime] = set()
        # 촘촘한 식은 _MAX_FIRES에서 잘린다. 목록 식을 합칠 때 잘린 뒤 구간에 다른 식의 실행만
        # 남으면 최대 간격이 부풀므로, 잘린 식들 중 가장 이른 마지막 실행까지만 본다.
        cutoff: _dt.datetime | None = None
        for expression in _expressions(raw):
            fires = []
            for moment in _fires(expression):
                fires.append(moment)
                if len(fires) >= _MAX_FIRES:
                    cutoff = moment if cutoff is None else min(cutoff, moment)
                    break
            moments.update(fires)
    except (ValueError, json.JSONDecodeError):
        return None
    ordered = sorted(moment for moment in moments if cutoff is None or moment <= cutoff)
    if len(ordered) < 2:
        return None
    gaps = [(later - earlier).total_seconds() for earlier, later in zip(ordered, ordered[1:], strict=False)]
    return CronPeriod(min_gap_seconds=min(gaps), max_gap_seconds=max(gaps))
