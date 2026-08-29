"""Manager-wide bounded readiness policy for host-loopback HTTP endpoints."""

from typing import Final

# Docker Compose container health and a host-published loopback socket are distinct
# runtime boundaries. Consumers may retry only a proven transient transport failure
# inside this one bounded window; received HTTP statuses and response contracts remain
# fail-closed at their own boundary.
LOOPBACK_HTTP_READINESS_ATTEMPTS: Final[int] = 30
LOOPBACK_HTTP_READINESS_RETRY_SECONDS: Final[float] = 1.0
