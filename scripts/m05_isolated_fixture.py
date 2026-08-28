#!/usr/bin/env python3
"""Manager M05 isolated one-shot용 최소권한 provider 후보 fixture.

Map API image 안에서 ``ktm_feature_dagster_runtime`` DSN 하나만 사용한다. owner DSN이나
role 전환 없이 Map의 일반 provider 적재 경로와 후보 procedure를 차례로 호출하므로, M04가
실제 UI로 승인한 수동 Feature는 직접 변경하지 않는다. stdout은 root driver가 메모리에서만
소비하며 일반 로그에 남기지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from kortravelmap.client import AsyncKorTravelMapClient
from kortravelmap.core.ids import make_payload_hash, make_source_record_key
from kortravelmap.dto import (
    Coordinate,
    Feature,
    FeatureBundle,
    FeatureKind,
    PlaceDetail,
    SourceLink,
    SourceRecord,
    SourceRole,
)
from kortravelmap.infra.db import (
    assert_runtime_db_privilege_boundary,
    make_async_engine,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_PROVIDER = "python-khoa-api"
_DATASET_KEY = "khoa_beaches"
_SOURCE_ENTITY_TYPE = "m05_isolated_provider_fixture"


def _async_dsn(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    if value.startswith("postgresql+asyncpg://"):
        return value
    raise SystemExit(2)


def _provider_bundle(*, suffix: str, fetched_at: datetime) -> FeatureBundle:
    provider_feature_id = f"m05i_provider_{suffix[:20]}"
    source_entity_id = f"m05i_source_entity_{suffix[:20]}"
    raw_data = {
        "fixture": "m05_isolated",
        "provider_feature_id": provider_feature_id,
        "source_entity_id": source_entity_id,
    }
    raw_payload_hash = make_payload_hash(raw_data)
    source_record_key = make_source_record_key(
        provider=_PROVIDER,
        dataset_key=_DATASET_KEY,
        source_entity_type=_SOURCE_ENTITY_TYPE,
        source_entity_id=source_entity_id,
        raw_payload_hash=raw_payload_hash,
    )
    feature = Feature(
        feature_id=provider_feature_id,
        kind=FeatureKind.PLACE,
        name="M05 isolated provider fixture",
        coord=Coordinate(lon=127.111222, lat=37.511222),
        category="01070300",
        marker_icon="marker",
        marker_color="P-01",
        detail=PlaceDetail(feature_id=provider_feature_id, place_kind="attraction"),
        created_at=fetched_at,
        updated_at=fetched_at,
    )
    return FeatureBundle(
        feature=feature,
        source_record=SourceRecord(
            provider=_PROVIDER,
            dataset_key=_DATASET_KEY,
            source_entity_type=_SOURCE_ENTITY_TYPE,
            source_entity_id=source_entity_id,
            raw_payload_hash=raw_payload_hash,
            raw_data=raw_data,
            fetched_at=fetched_at,
            imported_at=fetched_at,
            source_record_key=source_record_key,
        ),
        source_link=SourceLink(
            feature_id=provider_feature_id,
            source_record_key=source_record_key,
            source_role=SourceRole.PRIMARY,
            match_method="m05_isolated",
            confidence=100,
            created_at=fetched_at,
        ),
    )


async def _main(manual_feature_id: str) -> dict[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9_:-]{1,200}", manual_feature_id):
        raise SystemExit(2)
    runtime_dsn = os.environ.get("KOR_TRAVEL_MAP_PG_DSN", "")
    engine = make_async_engine(_async_dsn(runtime_dsn), pool_size=1)
    bundle = _provider_bundle(suffix=uuid4().hex, fetched_at=datetime.now(UTC))
    provider_feature_id = bundle.feature.feature_id
    try:
        await assert_runtime_db_privilege_boundary(
            engine, expected_login="ktm_feature_dagster_runtime"
        )
        async with AsyncKorTravelMapClient(engine) as client:
            result = await client.load_feature_bundles([bundle])
        if result.bundles_total != 1:
            raise SystemExit(3)
        async with AsyncSession(engine) as session, session.begin():
            candidate = (
                (
                    await session.execute(
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
