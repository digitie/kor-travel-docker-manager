import datetime
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
    return hashlib.sha256(api_key.strip().encode("utf-8")).hexdigest()


def public_api_key_matches(api_key: str, key_hashes: frozenset[str]) -> bool:
    key_hash = hash_public_api_key(api_key)
    return any(hmac.compare_digest(key_hash, stored_hash) for stored_hash in key_hashes)


def create_public_api_key(*, label: str | None, created_by: str | None) -> dict[str, object]:
    _ensure_db()
    api_key = generate_public_api_key()
    now = datetime.datetime.utcnow()
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
    now = datetime.datetime.utcnow()
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
    ttl = int(os.environ.get("KTDM_PUBLIC_API_KEY_CACHE_TTL_S", "30") or "30")
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


def invalidate_public_api_key_cache() -> None:
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
