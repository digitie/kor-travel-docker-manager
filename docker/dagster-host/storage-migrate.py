#!/usr/local/bin/python -I
"""공용 Dagster instance(`dagster_shared`)의 storage schema를 만들고 올린다.

이 one-shot만 schema를 만든다. 공용 `dagster.yaml`은 `should_autocreate_tables: false`라
webserver·daemon·code-server의 run worker가 빠진 table을 암묵 생성해 불완전한 migrate를
숨기지 못한다(Map ADR-102의 교훈). 그런데 `dagster instance migrate`(`DagsterInstance.upgrade`)는
storage마다 alembic upgrade와 데이터 migration(run storage `migrate`, event log `reindex_assets`,
schedule storage `migrate`)을 할 뿐 **빈 DB에 기본 table을 만들지 않는다** — 그래서 두 단계다.

1. bootstrap: 같은 `dagster.yaml`을 `should_autocreate_tables`만 켠 채로 읽어 Dagster 1.13.24 /
   dagster-postgres 0.29.24의 storage 생성자를 부른다. 생성자는 **그 storage의 주 table이 없을 때만**
   (`runs` / `event_logs` / `schedules`·`jobs` 둘 다) 전체 table을 만들고 alembic head를 stamp한 뒤
   그 storage의 데이터 migration·reindex를 돈다. 주 table이 있으면 run storage가 `instance_info`만
   빠졌을 때 만들 뿐 아무것도 하지 않는다 — 주 table만 있고 나머지가 빠진 DB는 여기서 고쳐지지
   않는다.
2. 그 뒤 원래 설정 그대로 `dagster instance migrate`를 exec한다. head면 alembic은 무연산이고 데이터
   migration은 이미 된 것을 건너뛴다. 이 프로세스의 종료 코드가 곧 one-shot의 결과이고, Manager의
   `ensure dagster`가 init step으로 그 코드를 본다(`config/docker-targets.yml`).

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
