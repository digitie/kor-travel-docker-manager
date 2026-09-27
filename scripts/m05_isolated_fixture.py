#!/usr/bin/env python3
"""Manager M05 isolated one-shot용 최소권한 provider 후보 fixture.

Map API image 안에서 서비스 role DSN 하나만 사용한다(Map ADR-100 — role은 ``ktm_feature_service``
하나다). owner DSN이나 role 전환 없이 Map의 일반 provider 적재 경로와 후보 procedure를 차례로 호출하므로,
M04가 실제 UI로 승인한 수동 Feature는 직접 변경하지 않는다. stdout은 root driver가 메모리에서만 소비하며
일반 로그에 남기지 않는다.

Map T-VN-39 뒤 Feature의 정본 키는 서버가 적재 시점에 발급하는 uuid이고, ``make_feature_id``의 텍스트는
alias다. provider Feature는 identity 축의 세 번째 성분인 ``provider_natural_key``를 싣는다(ADR-098). 후보
procedure는 uuid 둘을 받으므로 두 참조를 정본 키로 풀어 넘긴다.
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
from kortravelmap.core.ids import make_feature_id, make_payload_hash, make_source_record_key
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
from kortravelmap.infra.canonical_feature_ids import resolve_canonical_feature_ids
from kortravelmap.infra.db import (
    assert_runtime_db_privilege_boundary,
    make_async_engine,
)
from sqlalchemy import text
from sqlalchemy.engine import make_url
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
    source_entity_id = f"m05i_source_entity_{suffix[:20]}"
    # 텍스트 id는 provider 변환기와 같은 `make_feature_id` 산출물이어야 한다 — 적재가 그것을 legacy alias로
    # 등록하고 alias 모양(`f_*_<kind>_<16hex>`)을 검사한다. 정본 키(uuid)는 서버가 발급한다.
    provider_feature_id = make_feature_id(
        bjd_code=None,
        kind=FeatureKind.PLACE.value,
        category="01070300",
        source_type=_DATASET_KEY,
        source_natural_key=source_entity_id,
    )
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
        provider_natural_key=source_entity_id,
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


async def _main(manual_feature_ref: str) -> dict[str, str]:
    # 수동 Feature 참조는 정본 uuid든 legacy 텍스트 alias든 받는다 — 둘 다 아래에서 정본 키로 푼다.
    if not re.fullmatch(r"[A-Za-z0-9_:-]{1,200}", manual_feature_ref):
        raise SystemExit(2)
    runtime_dsn = _async_dsn(os.environ.get("KOR_TRAVEL_MAP_PG_DSN", ""))
    login = make_url(runtime_dsn).username
    if not login:
        raise SystemExit(2)
    engine = make_async_engine(runtime_dsn, pool_size=1)
    bundle = _provider_bundle(suffix=uuid4().hex, fetched_at=datetime.now(UTC))
    provider_feature_ref = bundle.feature.feature_id
    try:
        # 기대 로그인은 받은 DSN의 사용자다. Map은 서비스 role 밖의 로그인을 여전히 거부한다.
        await assert_runtime_db_privilege_boundary(engine, expected_login=login)
        async with AsyncKorTravelMapClient(engine) as client:
            result = await client.load_feature_bundles([bundle])
        if result.bundles_total != 1:
            raise SystemExit(3)
        async with AsyncSession(engine) as session, session.begin():
            canonical = await resolve_canonical_feature_ids(
                session, [manual_feature_ref, provider_feature_ref]
            )
            manual_feature_id = canonical[manual_feature_ref]
            provider_feature_id = canonical[provider_feature_ref]
            candidate = (
                (
                    await session.execute(
                        text(
                            """
                            CALL feature.record_manual_provider_dedup_candidate(
                              CAST(:manual_feature_id AS uuid), CAST(:provider_feature_id AS uuid),
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
            "manual_feature_id": str(manual_feature_id),
            "provider_feature_id": str(provider_feature_id),
            "provider_feature_ref": provider_feature_ref,
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
