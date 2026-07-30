from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

MAP_UI_PASSWORD_ENV = "KTDM_C6C_MAP_UI_ADMIN_PASSWORD"
MAP_UI_PASSWORD_HASH_ENV = "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH"
MAP_UI_SESSION_SECRET_ENV = "KOR_TRAVEL_MAP_UI_SESSION_SECRET"

MAP_PBKDF2_ALGORITHM = "pbkdf2_sha256"
MAP_PBKDF2_ITERATIONS = 310_000
MAP_PBKDF2_SALT_BYTES = 16
MAP_PBKDF2_DIGEST_BYTES = 32
_MAP_PBKDF2_PATTERN = re.compile(
    r"^pbkdf2_sha256\$(\d+)\$([A-Za-z0-9_-]+)\$([A-Za-z0-9_-]+)$"
)


@dataclass(frozen=True)
class MapUiAuthRotationResult:
    success: bool
    returncode: int
    phase: str
    audit_path: str | None = None
    journal_path: str | None = None
    rollback_state: str | None = None
    checks: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""

    def as_process_result(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "returncode": self.returncode,
            "phase": self.phase,
            "audit_path": self.audit_path,
            "journal_path": self.journal_path,
            "rollback_state": self.rollback_state,
            "checks": list(self.checks),
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def generate_map_pbkdf2_hash(password: str, *, salt: bytes | None = None) -> str:
    """Map UI가 요구하는 exact PBKDF2 hash 형식을 생성한다."""

    _validate_plaintext_password(password, label="new Map UI password")
    salt_bytes = secrets.token_bytes(MAP_PBKDF2_SALT_BYTES) if salt is None else salt
    if len(salt_bytes) != MAP_PBKDF2_SALT_BYTES:
        raise DeploymentContractError("Map UI password hash salt size is invalid")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        MAP_PBKDF2_ITERATIONS,
        dklen=MAP_PBKDF2_DIGEST_BYTES,
    )
    return (
        f"{MAP_PBKDF2_ALGORITHM}${MAP_PBKDF2_ITERATIONS}$"
        f"{_b64url_no_padding(salt_bytes)}${_b64url_no_padding(digest)}"
    )


def verify_map_pbkdf2_hash(password: str, encoded: str) -> bool:
    """Map UI PBKDF2 hash를 독립 검증한다."""

    match = _MAP_PBKDF2_PATTERN.fullmatch(encoded)
    if match is None:
        return False
    try:
        iterations = int(match.group(1))
        salt = _b64url_decode_no_padding(match.group(2))
        expected = _b64url_decode_no_padding(match.group(3))
    except (ValueError, binascii.Error):
        return False
    if (
        iterations != MAP_PBKDF2_ITERATIONS
        or len(salt) != MAP_PBKDF2_SALT_BYTES
        or len(expected) != MAP_PBKDF2_DIGEST_BYTES
    ):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=MAP_PBKDF2_DIGEST_BYTES,
    )
    return hmac.compare_digest(actual, expected)


def rotate_map_ui_auth(
    *,
    current_password: str,
    new_password: str,
    compose_path: str | None = None,
    env_path: str | None = None,
) -> MapUiAuthRotationResult:
    """Audited production Map UI credential rotation entrypoint."""

    del compose_path, env_path
    _validate_plaintext_password(current_password, label="current Map UI password")
    _validate_plaintext_password(new_password, label="new Map UI password")
    if current_password == new_password:
        raise DeploymentContractError(
            "new Map UI password must differ from the current password"
        )
    return MapUiAuthRotationResult(
        success=False,
        returncode=2,
        phase="not_implemented",
        stderr="Map UI auth rotation transaction is not implemented yet",
    )


def _validate_plaintext_password(password: str, *, label: str) -> None:
    if not isinstance(password, str) or len(password) < 12:
        raise DeploymentContractError(f"{label} is too short")
    if any(character.isspace() for character in password):
        raise DeploymentContractError(f"{label} must not contain whitespace")


def _b64url_no_padding(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode_no_padding(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
