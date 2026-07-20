import asyncio

from fastapi.testclient import TestClient

from nattvakten.app import CreateLeaseRequest, MachineController, create_app
from nattvakten.config import Settings
from nattvakten.power import PowerController


def make_client() -> TestClient:
    settings = Settings(
        api_token="test-token",
        default_lease_ttl_seconds=60,
        min_lease_ttl_seconds=30,
        max_lease_ttl_seconds=120,
        shutdown_grace_seconds=60,
    )
    return TestClient(create_app(settings))


def test_health_does_not_require_authentication() -> None:
    with make_client() as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lease_lifecycle_requires_authentication() -> None:
    headers = {"Authorization": "Bearer test-token"}
    with make_client() as client:
        unauthorized = client.post("/v1/leases", json={"client_name": "worker"})
        created = client.post(
            "/v1/leases",
            headers=headers,
            json={"client_name": "worker", "ttl_seconds": 60},
        )
        lease_id = created.json()["id"]
        renewed = client.put(
            f"/v1/leases/{lease_id}",
            headers=headers,
            json={"client_name": "ignored", "ttl_seconds": 90},
        )
        released = client.delete(f"/v1/leases/{lease_id}", headers=headers)
        status_response = client.get("/v1/status", headers=headers)

    assert unauthorized.status_code == 401
    assert created.status_code == 201
    assert renewed.status_code == 200
    assert renewed.json()["client_name"] == "worker"
    assert released.status_code == 204
    assert status_response.json()["active_lease_count"] == 0


def test_lease_ttl_is_bounded() -> None:
    headers = {"Authorization": "Bearer test-token"}
    with make_client() as client:
        response = client.post(
            "/v1/leases",
            headers=headers,
            json={"client_name": "worker", "ttl_seconds": 10},
        )

    assert response.status_code == 422
    assert "ttl_seconds must be between 30 and 120" in response.json()["detail"]


def test_maintenance_requires_authentication_and_cancels_shutdown() -> None:
    headers = {"Authorization": "Bearer test-token"}
    with make_client() as client:
        controller = client.app.state.controller
        controller.state = "shutdown_pending"
        controller.shutdown_at = None

        unauthorized = client.put("/v1/maintenance")
        enabled = client.put("/v1/maintenance", headers=headers, json={"ttl_seconds": 60})
        status_response = client.get("/v1/status", headers=headers)
        disabled = client.delete("/v1/maintenance", headers=headers)

    assert unauthorized.status_code == 401
    assert enabled.status_code == 200
    assert enabled.json()["active"] is True
    assert status_response.json()["state"] == "maintenance"
    assert status_response.json()["maintenance_active"] is True
    assert status_response.json()["shutdown_at"] is None
    assert disabled.status_code == 204


class RecordingPowerController:
    def __init__(self) -> None:
        self.calls = 0

    def power_off(self) -> None:
        self.calls += 1


def test_power_off_writes_a_request_file(tmp_path) -> None:
    request_path = tmp_path / "poweroff.request"

    PowerController(enabled=True, request_path=str(request_path)).power_off()

    assert request_path.exists()


def test_power_off_is_a_noop_when_disabled(tmp_path) -> None:
    request_path = tmp_path / "poweroff.request"

    PowerController(enabled=False, request_path=str(request_path)).power_off()

    assert not request_path.exists()


async def test_lease_cancels_pending_shutdown() -> None:
    settings = Settings(
        api_token="test-token",
        default_lease_ttl_seconds=60,
        min_lease_ttl_seconds=30,
        max_lease_ttl_seconds=120,
        shutdown_grace_seconds=60,
    )
    controller = MachineController(settings, RecordingPowerController())
    controller.state = "shutdown_pending"
    controller.shutdown_at = None

    controller.create_lease(CreateLeaseRequest(client_name="worker", ttl_seconds=60))

    assert controller.state == "ready"
    assert controller.shutdown_at is None

    await asyncio.sleep(0)

