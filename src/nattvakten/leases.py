from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4


@dataclass(frozen=True)
class Lease:
    id: str
    client_name: str
    expires_at: datetime


class LeaseManager:
    def __init__(self, minimum_ttl_seconds: int, maximum_ttl_seconds: int) -> None:
        self._minimum_ttl_seconds = minimum_ttl_seconds
        self._maximum_ttl_seconds = maximum_ttl_seconds
        self._leases: dict[str, Lease] = {}

    def create(self, client_name: str, ttl_seconds: int) -> Lease:
        self._validate_ttl(ttl_seconds)
        lease = Lease(
            id=str(uuid4()),
            client_name=client_name,
            expires_at=self._now() + timedelta(seconds=ttl_seconds),
        )
        self._leases[lease.id] = lease
        return lease

    def renew(self, lease_id: str, ttl_seconds: int) -> Lease | None:
        self._validate_ttl(ttl_seconds)
        self.remove_expired()
        lease = self._leases.get(lease_id)
        if lease is None:
            return None
        renewed_lease = Lease(
            id=lease.id,
            client_name=lease.client_name,
            expires_at=self._now() + timedelta(seconds=ttl_seconds),
        )
        self._leases[lease_id] = renewed_lease
        return renewed_lease

    def release(self, lease_id: str) -> bool:
        self.remove_expired()
        return self._leases.pop(lease_id, None) is not None

    def active(self) -> list[Lease]:
        self.remove_expired()
        return sorted(self._leases.values(), key=lambda lease: lease.expires_at)

    def remove_expired(self) -> None:
        now = self._now()
        expired_ids = [
            lease_id
            for lease_id, lease in self._leases.items()
            if lease.expires_at <= now
        ]
        for lease_id in expired_ids:
            del self._leases[lease_id]

    def _validate_ttl(self, ttl_seconds: int) -> None:
        if not self._minimum_ttl_seconds <= ttl_seconds <= self._maximum_ttl_seconds:
            raise ValueError(
                f"ttl_seconds must be between {self._minimum_ttl_seconds} and "
                f"{self._maximum_ttl_seconds}"
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
