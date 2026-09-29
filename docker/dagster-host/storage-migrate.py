#!/usr/local/bin/python -I
"""공용 Dagster instance(`dagster_shared`)의 storage schema를 만들고 올린다.

이 one-shot만 schema를 만든다. 공용 `dagster.yaml`은 `should_autocreate_tables: false`라
webserver·daemon·code-server의 run worker가 빠진 table을 암묵 생성해 불완전한 migrate를
숨기지 못한다(Map ADR-102의 교훈). 그런데 `dagster instance migrate`는 alembic
upgrade만 하므로 **빈 DB에 table을 만들지 않는다** — 그래서 두 단계다.

1. 빈 DB면 bootstrap: 같은 `dagster.yaml`을 `should_autocreate_tables`만 켠 채로 읽어
   Dagster 자신의 storage 생성자가 table을 만들고 alembic head를 stamp하게 한다. table이
   이미 있으면 생성자는 아무것도 만들지 않는다.
2. 그 뒤 원래 설정 그대로 `dagster instance migrate`를 실행한다(멱등 — head면 무연산).

`dagster.yaml`이 없거나 storage가 PostgreSQL이 아니면 거부한다. 파일이 없을 때 Dagster는
`$DAGSTER_HOME` 아래 SQLite로 조용히 떨어지는데, 그 위의 migrate는 성공으로 보인다.
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

import yaml


def _fail(message: str) -> int:
    print(f"dagster-shared-storage-migrate: {message}", file=sys.stderr)
    return 2


def main() -> int:
    home = os.environ.get("DAGSTER_HOME", "")
    if not home:
        return _fail("DAGSTER_HOME is not set")
    config_path = Path(home) / "dagster.yaml"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return _fail(f"cannot read {config_path}: {exc.strerror}")
    storage = config.get("storage") if isinstance(config, dict) else None
    if not isinstance(storage, dict) or set(storage) != {"postgres"}:
        return _fail(f"{config_path} must declare exactly `storage: postgres`")

    from dagster import DagsterInstance
    from dagster._core.instance.ref import InstanceRef

    bootstrap_storage = copy.deepcopy(storage)
    bootstrap_storage["postgres"]["should_autocreate_tables"] = True
    # overrides는 최상위 키 단위로 병합된다 — `storage` 전체를 넘긴다.
    ref = InstanceRef.from_dir(home, overrides={"storage": bootstrap_storage})
    with DagsterInstance.from_ref(ref) as instance:
        # 세 storage를 실제로 만든다(생성자가 bootstrap을 한다).
        storages = (
            instance.run_storage,
            instance.event_log_storage,
            instance.schedule_storage,
        )
        names = sorted(type(item).__name__ for item in storages)
        if not all(name.startswith("Postgres") for name in names):
            return _fail(f"storage is not PostgreSQL: {names}")

    sys.stdout.flush()
    os.execvp("dagster", ["dagster", "instance", "migrate"])
    return 0  # pragma: no cover - execvp는 돌아오지 않는다


if __name__ == "__main__":
    raise SystemExit(main())
