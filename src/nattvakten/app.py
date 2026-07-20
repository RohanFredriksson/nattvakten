from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import asyncio
import secrets
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from .config import Settings
from .leases import Lease, LeaseManager
from .power import PowerController


LifecycleState = Literal[
    "starting",
    "preparing",
    "ready",
    "maintenance",
    "shutdown_pending",
    "powering_off",
    "failed",
]


class CreateLeaseRequest(BaseModel):
    client_name: str = Field(min_length=1, max_length=100)
    ttl_seconds: int | None = None


class EnableMaintenanceRequest(BaseModel):
    ttl_seconds: int = Field(default=3600, ge=1, le=86400)


class LeaseResponse(BaseModel):
    id: str
    client_name: str
    expires_at: datetime


class StatusResponse(BaseModel):
    boot_id: str
    state: LifecycleState
    active_lease_count: int
    shutdown_at: datetime | None
    maintenance_active: bool
    maintenance_expires_at: datetime | None
    readiness: dict[str, str]


class MaintenanceResponse(BaseModel):
    active: bool
    expires_at: datetime | None


class MachineController:
    def __init__(self, settings: Settings, power: PowerController | None = None) -> None:
        self.settings = settings
        self.boot_id = str(uuid4())
        self.leases = LeaseManager(
            settings.min_lease_ttl_seconds, settings.max_lease_ttl_seconds
        )
        self.power = power or PowerController(
            settings.poweroff_enabled, settings.poweroff_request_path
        )
        self.state: LifecycleState = "starting"
        self.shutdown_at: datetime | None = None
        self.maintenance_expires_at: datetime | None = None
        self._monitor_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.state = "preparing"
        self.state = "ready"
        self._monitor_task = asyncio.create_task(self._monitor_leases())

    async def stop(self) -> None:
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    def create_lease(self, request: CreateLeaseRequest) -> Lease:
        ttl_seconds = request.ttl_seconds or self.settings.default_lease_ttl_seconds
        lease = self.leases.create(request.client_name, ttl_seconds)
        self._cancel_shutdown()
        return lease

    def renew_lease(self, lease_id: str, ttl_seconds: int | None) -> Lease | None:
        lease = self.leases.renew(
            lease_id, ttl_seconds or self.settings.default_lease_ttl_seconds
        )
        if lease is not None:
            self._cancel_shutdown()
        return lease

    def enable_maintenance(self, ttl_seconds: int) -> MaintenanceResponse:
        self.maintenance_expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        self._cancel_shutdown()
        self.state = "maintenance"
        return self.maintenance_status()

    def disable_maintenance(self) -> None:
        self.maintenance_expires_at = None
        if self.state == "maintenance":
            self.state = "ready"

    def maintenance_status(self) -> MaintenanceResponse:
        return MaintenanceResponse(
            active=self._maintenance_active(),
            expires_at=self.maintenance_expires_at,
        )

    def status(self) -> StatusResponse:
        active_leases = self.leases.active()
        maintenance = self.maintenance_status()
        return StatusResponse(
            boot_id=self.boot_id,
            state=self.state,
            active_lease_count=len(active_leases),
            shutdown_at=self.shutdown_at,
            maintenance_active=maintenance.active,
            maintenance_expires_at=maintenance.expires_at,
            readiness={"controller": "ready" if self.state == "ready" else self.state},
        )

    async def _monitor_leases(self) -> None:
        while True:
            if self._maintenance_active():
                self._cancel_shutdown()
            elif self.state == "maintenance":
                self.state = "ready"
            active_leases = self.leases.active()
            if active_leases:
                self._cancel_shutdown()
            elif self.state == "ready":
                self.state = "shutdown_pending"
                self.shutdown_at = datetime.now(UTC) + timedelta(
                    seconds=self.settings.shutdown_grace_seconds
                )
            elif (
                self.state == "shutdown_pending"
                and self.shutdown_at is not None
                and datetime.now(UTC) >= self.shutdown_at
            ):
                self.state = "powering_off"
                self.power.power_off()
                self.shutdown_at = None
            await asyncio.sleep(1)

    def _cancel_shutdown(self) -> None:
        if self.state == "shutdown_pending":
            self.state = "ready"
        self.shutdown_at = None

    def _maintenance_active(self) -> bool:
        if self.maintenance_expires_at is None:
            return False
        if datetime.now(UTC) < self.maintenance_expires_at:
            return True
        self.maintenance_expires_at = None
        if self.state == "maintenance":
            self.state = "ready"
        return False


def _to_response(lease: Lease) -> LeaseResponse:
    return LeaseResponse(
        id=lease.id,
        client_name=lease.client_name,
        expires_at=lease.expires_at,
    )


def create_app(
    settings: Settings | None = None, power: PowerController | None = None
) -> FastAPI:
    controller = MachineController(settings or Settings.from_environment(), power)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await controller.start()
        yield
        await controller.stop()

    app = FastAPI(title="Nattvakten", lifespan=lifespan)
    app.state.controller = controller

    def require_token(
        authorization: str | None = Header(default=None),
    ) -> None:
        expected_token = controller.settings.api_token
        supplied_token = authorization.removeprefix("Bearer ") if authorization else ""
        if not expected_token or not secrets.compare_digest(supplied_token, expected_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/status", response_model=StatusResponse, dependencies=[Depends(require_token)])
    def get_status() -> StatusResponse:
        return controller.status()

    @app.put(
        "/v1/maintenance",
        response_model=MaintenanceResponse,
        dependencies=[Depends(require_token)],
    )
    def enable_maintenance(request: EnableMaintenanceRequest) -> MaintenanceResponse:
        return controller.enable_maintenance(request.ttl_seconds)

    @app.delete(
        "/v1/maintenance",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_token)],
    )
    def disable_maintenance() -> None:
        controller.disable_maintenance()

    @app.post(
        "/v1/leases",
        response_model=LeaseResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_token)],
    )
    def create_lease(request: CreateLeaseRequest) -> LeaseResponse:
        try:
            return _to_response(controller.create_lease(request))
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))

    @app.put(
        "/v1/leases/{lease_id}",
        response_model=LeaseResponse,
        dependencies=[Depends(require_token)],
    )
    def renew_lease(lease_id: str, request: CreateLeaseRequest) -> LeaseResponse:
        try:
            lease = controller.renew_lease(lease_id, request.ttl_seconds)
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))
        if lease is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lease not found")
        return _to_response(lease)

    @app.delete(
        "/v1/leases/{lease_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_token)])
    def release_lease(lease_id: str) -> None:
        if not controller.leases.release(lease_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lease not found")

    return app


app = create_app()
