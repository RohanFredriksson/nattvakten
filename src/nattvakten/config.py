from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    api_token: str
    default_lease_ttl_seconds: int = 300
    min_lease_ttl_seconds: int = 30
    max_lease_ttl_seconds: int = 900
    shutdown_grace_seconds: int = 60
    poweroff_enabled: bool = False
    poweroff_request_path: str = "/run/nattvakten/poweroff.request"

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            api_token=os.environ.get("NATTVAKTEN_API_TOKEN", ""),
            default_lease_ttl_seconds=int(
                os.environ.get("NATTVAKTEN_DEFAULT_LEASE_TTL_SECONDS", "300")
            ),
            min_lease_ttl_seconds=int(
                os.environ.get("NATTVAKTEN_MIN_LEASE_TTL_SECONDS", "30")
            ),
            max_lease_ttl_seconds=int(
                os.environ.get("NATTVAKTEN_MAX_LEASE_TTL_SECONDS", "900")
            ),
            shutdown_grace_seconds=int(
                os.environ.get("NATTVAKTEN_SHUTDOWN_GRACE_SECONDS", "60")
            ),
            poweroff_enabled=os.environ.get(
                "NATTVAKTEN_POWEROFF_ENABLED", "false"
            ).lower()
            in {"1", "true", "yes"},
            poweroff_request_path=os.environ.get(
                "NATTVAKTEN_POWEROFF_REQUEST_PATH", "/run/nattvakten/poweroff.request"
            ),
        )
