import hashlib
import hmac
import os
import secrets
import string
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

from sqlalchemy import select

from kor_travel_docker_manager import database
from kor_travel_docker_manager._time import utcnow
from kor_travel_docker_manager.models import Base, PublicApiKey

PUBLIC_API_KEY_QUERY_PARAM = "key"
PUBLIC_API_KEY_LENGTH = 32
PUBLIC_API_KEY_ALPHABET = string.ascii_letters + string.digits

_db_initialized = False
_db_engine_id: int | None = None
_active_key_cache: "_ActiveKeyCacheEntry | None" = None


@dataclass(frozen=True, slots=True)
class _ActiveKeyCacheEntry:
    hashes: frozenset[str]
    expires_at: float


def generate_public_api_key() -> str:
    return "".join(secrets.choice(PUBLIC_API_KEY_ALPHABET) for _ in range(PUBLIC_API_KEY_LENGTH))


def hash_public_api_key(api_key: str) -> str:
    # 키는 32자 CSPRNG 토큰(~190비트)이라 brute-force가 불가능하므로, 활성 해시 집합에 대한
    # O(1) 멤버십 검사를 위해 의도적으로 빠른 무염 SHA-256을 사용한다(저엔트로피 패스워드용
    # 느린 KDF는 불필요). 평문 키는 저장/로깅하지 않는다.
    return hashlib.sha256(api_key.strip().encode("utf-8")).hexdigest()


def public_api_key_matches(api_key: str, key_hashes: frozenset[str]) -> bool:
    key_hash = hash_public_api_key(api_key)
    return any(hmac.compare_digest(key_hash, stored_hash) for stored_hash in key_hashes)


def create_public_api_key(*, label: str | None, created_by: str | None) -> dict[str, object]:
    _ensure_db()
    api_key = generate_public_api_key()
    now = utcnow()
    item = PublicApiKey(
        public_api_key_id=str(uuid4()),
        key_hash=hash_public_api_key(api_key),
        key_hint=api_key[-6:],
        label=_normalize_label(label),
        state="active",
        created_at=now,
        created_by=created_by,
    )
    with database.get_db_session() as session:
        session.add(item)
        session.commit()
        session.refresh(item)
        summary = _public_api_key_summary(item)
    invalidate_public_api_key_cache()
    return {"key": api_key, "item": summary}


def list_public_api_keys(*, limit: int = 100) -> list[dict[str, object]]:
    _ensure_db()
    with database.get_db_session() as session:
        rows = session.scalars(
            select(PublicApiKey).order_by(PublicApiKey.created_at.desc()).limit(limit)
        ).all()
        return [_public_api_key_summary(row) for row in rows]


def revoke_public_api_key(public_api_key_id: str, *, revoked_by: str | None) -> dict[str, object]:
    _ensure_db()
    now = utcnow()
    with database.get_db_session() as session:
        row = session.scalars(
            select(PublicApiKey).where(
                PublicApiKey.public_api_key_id == public_api_key_id,
                PublicApiKey.state == "active",
            )
        ).first()
        if row is None:
            raise KeyError(public_api_key_id)
        row.state = "revoked"
        row.revoked_at = now
        row.revoked_by = revoked_by
        session.commit()
        session.refresh(row)
        summary = _public_api_key_summary(row)
    invalidate_public_api_key_cache()
    return summary


def active_public_api_key_hashes() -> frozenset[str]:
    global _active_key_cache
    _ensure_db()
    now = monotonic()
    ttl = _cache_ttl_seconds()
    if _active_key_cache is not None and _active_key_cache.expires_at > now:
        return _active_key_cache.hashes
    with database.get_db_session() as session:
        hashes = frozenset(
            session.scalars(
                select(PublicApiKey.key_hash).where(PublicApiKey.state == "active")
            ).all()
        )
    _active_key_cache = _ActiveKeyCacheEntry(hashes=hashes, expires_at=now + max(ttl, 0))
    return hashes


def _cache_ttl_seconds() -> int:
    """활성 키 캐시 TTL(초)을 환경변수에서 안전하게 읽는다.

    ``KTDM_PUBLIC_API_KEY_CACHE_TTL_S`` 에 ``30s`` 같은 비숫자 값이 들어와도
    ValueError로 키 검증이 매 요청 500으로 떨어지지 않도록 기본값(30)으로 폴백하고
    sane 범위로 clamp 한다.
    """
    raw = os.environ.get("KTDM_PUBLIC_API_KEY_CACHE_TTL_S", "30")
    try:
        ttl = int(raw)
    except (TypeError, ValueError):
        return 30
    return max(0, min(ttl, 3600))


def invalidate_public_api_key_cache() -> None:
    # NOTE: 캐시는 프로세스 로컬이다. 매니저 백엔드는 단일 프로세스(단일 워커)로 구동하는 것을
    # 전제로 한다. 다중 워커로 구동하면 한 워커에서의 키 폐기가 다른 워커에는 최대
    # KTDM_PUBLIC_API_KEY_CACHE_TTL_S 초 동안 즉시 반영되지 않으므로, 멀티워커가 필요해지면
    # 요청당 DB 조회나 공유 캐시로 전환해야 한다.
    global _active_key_cache
    _active_key_cache = None


def configured_vworld_fallback_hashes() -> frozenset[str]:
    values = (
        os.environ.get("KOR_TRAVEL_GEO_VWORLD_API_KEY", ""),
        os.environ.get("NEXT_PUBLIC_VWORLD_API_KEY", ""),
    )
    return frozenset(hash_public_api_key(value) for value in values if value.strip())


def public_api_key_is_valid(api_key: str) -> bool:
    key = api_key.strip()
    if not key or len(key) > 128:
        return False
    active_hashes = active_public_api_key_hashes()
    effective_hashes = active_hashes or configured_vworld_fallback_hashes()
    return bool(effective_hashes and public_api_key_matches(key, effective_hashes))


def _ensure_db() -> None:
    global _db_engine_id, _db_initialized
    engine_id = id(database.engine)
    if _db_initialized and _db_engine_id == engine_id:
        return
    Base.metadata.create_all(bind=database.engine)
    _db_initialized = True
    _db_engine_id = engine_id


def _public_api_key_summary(row: PublicApiKey) -> dict[str, object]:
    return {
        "public_api_key_id": row.public_api_key_id,
        "label": row.label,
        "key_hint": row.key_hint,
        "state": row.state,
        "created_at": row.created_at.isoformat(),
        "created_by": row.created_by,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "revoked_by": row.revoked_by,
    }


def _normalize_label(label: str | None) -> str | None:
    if label is None:
        return None
    trimmed = label.strip()
    return trimmed[:80] if trimmed else None
