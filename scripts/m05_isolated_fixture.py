#!/usr/bin/env python3
"""Manager M05 isolated one-shot용 provider 후보 fixture.

이 helper는 Map API image 내부에서만 실행된다. M04가 실제 UI로 승인한 수동 Feature를
직접 변경하지 않고, 그 Feature와 연결될 provider provenance + immutable dedup candidate만
추가한다. stdout은 root driver가 메모리에서만 소비하며 일반 로그로 남기지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from uuid import UUID, uuid4

from kortravelmap.infra.db import make_async_engine
from sqlalchemy import text


def _async_dsn(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    if value.startswith("postgresql+asyncpg://"):
        return value
    raise SystemExit(2)


async def _main(manual_feature_id: str) -> dict[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9_:-]{1,200}", manual_feature_id):
        raise SystemExit(2)
    bootstrap_dsn = os.environ.get("KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN", "")
    engine = make_async_engine(_async_dsn(bootstrap_dsn), pool_size=1)
    suffix = uuid4().hex
    provider_feature_id = f"m05i_provider_{suffix[:20]}"
    source_entity_key = f"m05i_source_entity_{suffix[:20]}"
    source_record_key = f"m05i_source_record_{suffix[:20]}"
    try:
        async with engine.begin() as connection:
            manual_uuid = await connection.scalar(
                text(
                    "SELECT feature_uuid::text FROM feature.features "
                    "WHERE feature_id = :feature_id"
                ),
                {"feature_id": manual_feature_id},
            )
            if not isinstance(manual_uuid, str):
                raise SystemExit(3)
            UUID(manual_uuid)
            await connection.execute(
                text(
                    """
                    INSERT INTO feature.features (
                      feature_id, kind, name, category, coord, coord_precision_digits
                    ) VALUES (
                      :feature_id, 'place', 'M05 isolated provider fixture', '01070300',
                      x_extension.ST_SetSRID(x_extension.ST_MakePoint(127.111222, 37.511222), 4326), 6
                    )
                    """
                ),
                {"feature_id": provider_feature_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO feature.feature_places (
                      feature_id, feature_uuid, kind, place_kind, facility_info, reviews_link, payload
                    ) SELECT feature_id, feature_uuid, kind, 'attraction',
                             '{}'::jsonb, '{}'::jsonb, '{}'::jsonb
                      FROM feature.features WHERE feature_id = :feature_id
                    """
                ),
                {"feature_id": provider_feature_id},
            )
            dataset_id = await connection.scalar(
                text(
                    """
                    INSERT INTO provider_sync.provider_datasets (
                      provider, dataset_key, display_name, source_kind, is_active, capabilities
                    ) VALUES (
                      :provider, :dataset_key, 'M05 isolated provider fixture', 'system', true,
                      jsonb_build_object('schema_version', 1, 'produces', '[]'::jsonb, 'extensions', '{}'::jsonb)
                    ) RETURNING provider_dataset_id
                    """
                ),
                {
                    "provider": f"m05i-{suffix[:12]}",
                    "dataset_key": f"m05i-{suffix[:12]}",
                },
            )
            if not isinstance(dataset_id, int):
                raise SystemExit(3)
            await connection.execute(
                text(
                    """
                    INSERT INTO provider_sync.source_entities (
                      source_entity_key, provider_dataset_id, source_entity_type, source_entity_id,
                      first_seen_at, last_seen_at
                    ) VALUES (:key, :dataset_id, 'place', :entity_id, clock_timestamp(), clock_timestamp())
                    """
                ),
                {
                    "key": source_entity_key,
                    "dataset_id": dataset_id,
                    "entity_id": f"m05i-{suffix[:12]}",
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO provider_sync.source_records (
                      source_record_key, source_entity_key, raw_payload_hash, raw_data, fetched_at, imported_at
                    ) VALUES (:record_key, :entity_key, repeat('b', 64), '{}'::jsonb, clock_timestamp(), clock_timestamp())
                    """
                ),
                {"record_key": source_record_key, "entity_key": source_entity_key},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO provider_sync.source_entity_heads (
                      source_entity_key, current_source_record_key, observed_at, lineage_key
                    ) VALUES (:entity_key, :record_key, clock_timestamp(), :lineage_key)
                    """
                ),
                {
                    "entity_key": source_entity_key,
                    "record_key": source_record_key,
                    "lineage_key": f"m05i-{suffix[:12]}",
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO provider_sync.source_links (
                      feature_id, source_entity_key, source_role, match_method, confidence
                    ) VALUES (:feature_id, :entity_key, 'primary', 'm05-isolated', 100)
                    """
                ),
                {"feature_id": provider_feature_id, "entity_key": source_entity_key},
            )
            # candidate writer는 ordinary DAGSTER runtime 권한만 가진다. bootstrap owner가
            # schema를 쓰는 fixture가 이 경계를 우회하지 않도록 procedure 호출만 그 role로
            # 낮춘다.
            await connection.execute(text("SET LOCAL ROLE ktm_feature_dagster_runtime"))
            candidate = (
                (
                    await connection.execute(
                        text(
                            """
                        CALL feature.record_manual_provider_dedup_candidate(
                          CAST(:manual_feature_id AS text), CAST(:provider_feature_id AS text),
                          CAST(:scores AS jsonb), CAST(:causation AS jsonb), NULL::uuid, NULL::text
                        )
                        """
                        ),
                        {
                            "manual_feature_id": manual_feature_id,
                            "provider_feature_id": provider_feature_id,
                            "scores": json.dumps(
                                {
                                    "name_score": 0.95,
                                    "spatial_score": 0.97,
                                    "category_score": 0.80,
                                    "total_score": 0.93,
                                    "distance_meters": 12.345,
                                    "scorer_input_sha256": "a" * 64,
                                }
                            ),
                            "causation": json.dumps(
                                {"scope": "m05-isolated", "input_count": 1}
                            ),
                        },
                    )
                )
                .mappings()
                .one()
            )
            case_id = candidate.get("o_case_id") or candidate.get("case_id")
            if not isinstance(case_id, UUID):
                raise SystemExit(3)
            return {
                "case_id": str(case_id),
                "manual_feature_id": manual_feature_id,
                "provider_feature_id": provider_feature_id,
            }
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    print(
        json.dumps(
            asyncio.run(_main(sys.argv[1])), separators=(",", ":"), sort_keys=True
        )
    )
