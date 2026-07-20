# Nattvakten

Nattvakten is a FastAPI daemon for a Wake-on-LAN Ubuntu host. Each boot is a new session: it reports readiness, gives dependent programs short-lived leases, and powers off only after every lease expires and a grace period passes.

## API contract

`GET /healthz` is unauthenticated process liveness. All `/v1` endpoints require `Authorization: Bearer <token>`.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/status` | Boot ID, lifecycle state, readiness, active lease count, and planned shutdown time |
| `POST /v1/leases` | Acquire a lease with `client_name` and optional `ttl_seconds` |
| `PUT /v1/leases/{id}` | Renew an existing lease using `ttl_seconds` |
| `DELETE /v1/leases/{id}` | Release a lease early |

Lease TTLs are bounded by configuration. Leases live only in memory, so a full power cycle intentionally clears them.

## Local development

Docker is the default local workflow. Configure a local token, then start the API with live reload:

```bash
cp .env.example .env
docker compose -f compose.dev.yaml up --build
```

Nattvakten listens on port `8765` by default. When it is already in use, choose another local port:

```bash
NATTVAKTEN_DEV_PORT=18765 docker compose -f compose.dev.yaml up --build
```

Run focused tests in the same development container with:

```bash
docker compose -f compose.dev.yaml run --rm nattvakten python -m pytest -q
```

The development container mounts the source code read-only and does not receive the host D-Bus socket, so it cannot power off the host. `NATTVAKTEN_POWEROFF_ENABLED` should remain `false` in `.env`.

Direct Python remains useful for editor debugging or a quick test run:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
set -a; . ./.env; set +a
.venv/bin/uvicorn nattvakten.app:app --app-dir src --reload
PYTHONPATH=src .venv/bin/python -m pytest -q
```

## Docker deployment

These steps target Ubuntu 24.04 or newer and assume this repository has been copied or cloned to the server. Wake-on-LAN must be configured separately in firmware and on the network interface before the final validation.

Deployment responsibilities are deliberately split:

- Docker builds and runs the API, including Python and its dependencies.
- Compose provides the API configuration and a read-only container with no Linux capabilities.
- systemd starts and stops the Compose project at boot.
- The container runs with the host `nattvakten` UID and can reach only the system D-Bus socket. Polkit permits that identity to start the fixed `nattvakten-poweroff.service`, not arbitrary root commands.

### 1. Install Docker and host tools

Run on the new Ubuntu server:

```bash
sudo apt update
sudo apt install --yes docker.io docker-compose-v2 ethtool curl
sudo systemctl enable --now docker.service
```

### 2. Copy the application

Clone the repository on the server. No host Python environment or package installation is needed.

```bash
sudo git clone https://github.com/RohanFredriksson/nattvakten.git /opt/nattvakten
```

### 3. Create the service account and configuration

Generate a token on the server. Save it somewhere secure; clients need this exact value in their `Authorization: Bearer` header. The UID and GID make the API container appear as the restricted `nattvakten` account to the host D-Bus and polkit.

```bash
TOKEN=$(openssl rand -hex 32)
echo "$TOKEN"
sudo useradd --system --home /var/lib/nattvakten --shell /usr/sbin/nologin nattvakten
sudo install -d -o nattvakten -g nattvakten /etc/nattvakten
NATTVAKTEN_UID=$(id -u nattvakten)
NATTVAKTEN_GID=$(id -g nattvakten)
sudo sh -c "cat > /etc/nattvakten/nattvakten.env" <<EOF
NATTVAKTEN_API_TOKEN=$TOKEN
NATTVAKTEN_DEFAULT_LEASE_TTL_SECONDS=300
NATTVAKTEN_MIN_LEASE_TTL_SECONDS=30
NATTVAKTEN_MAX_LEASE_TTL_SECONDS=900
NATTVAKTEN_SHUTDOWN_GRACE_SECONDS=60
NATTVAKTEN_POWEROFF_ENABLED=false
NATTVAKTEN_PORT=8765
NATTVAKTEN_UID=$NATTVAKTEN_UID
NATTVAKTEN_GID=$NATTVAKTEN_GID
EOF
sudo chown root:nattvakten /etc/nattvakten/nattvakten.env
sudo chmod 640 /etc/nattvakten/nattvakten.env
```

Keep power-off disabled until the API and WOL path work correctly.

### 4. Install and start the container service

The systemd unit builds and starts the container. When power-off is enabled, the API container uses the host D-Bus to start only the fixed, root-owned `nattvakten-poweroff.service`; it cannot run arbitrary commands as root.

```bash
sudo install -m 644 /opt/nattvakten/deploy/nattvakten.service /etc/systemd/system/nattvakten.service
sudo install -m 644 /opt/nattvakten/deploy/nattvakten-poweroff.service /etc/systemd/system/nattvakten-poweroff.service
sudo install -m 644 /opt/nattvakten/deploy/49-nattvakten-poweroff.rules /etc/polkit-1/rules.d/49-nattvakten-poweroff.rules
sudo systemctl daemon-reload
sudo systemctl enable --now nattvakten.service
```

Do not replace the polkit rule with a broad passwordless `sudo` rule or mount the Docker socket in the container. The D-Bus mount is necessary for the fixed power-off request; polkit limits it to that one systemd unit.

### 5. Verify the API before enabling power-off

Run on the server:

```bash
curl http://127.0.0.1:8765/healthz
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/v1/status
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"client_name":"manual-check","ttl_seconds":60}' \
  http://127.0.0.1:8765/v1/leases
sudo journalctl -u nattvakten.service --since "5 minutes ago"
sudo docker compose --env-file /etc/nattvakten/nattvakten.env -f /opt/nattvakten/compose.yaml logs --tail=100
```

The status response should report a `boot_id`, `ready` state, and an active lease count of `1`. Delete the lease using its returned ID, then wait through the grace period. Because power-off remains disabled, the status changes to `powering_off` but the machine stays on.

### 6. Enable and test real power-off

First validate the root helper from the service account. This powers the host off immediately, so run it only when it is safe to do so:

```bash
sudo -u nattvakten /usr/bin/systemctl start nattvakten-poweroff.service
```

After waking the machine and confirming it boots normally, enable controller-initiated power-off:

```bash
sudo sed -i 's/^NATTVAKTEN_POWEROFF_ENABLED=false$/NATTVAKTEN_POWEROFF_ENABLED=true/' /etc/nattvakten/nattvakten.env
sudo systemctl restart nattvakten.service
```

Acquire a lease, let it expire, and confirm the server powers off after `NATTVAKTEN_SHUTDOWN_GRACE_SECONDS`. Send a WOL packet and repeat the cycle.

### Updating

Pull the new revision, then restart the service. Compose rebuilds the image before bringing the API back up.

```bash
cd /opt/nattvakten
sudo git pull --ff-only
sudo systemctl restart nattvakten.service
```

## Hardware and network validation

Enable Wake-on-LAN in firmware and confirm the NIC wake mode with `ethtool`. Validate the intended network path, since WOL broadcasts commonly do not cross routers. Then verify repeated cycles: WOL, boot, readiness, lease acquire/renew/release, grace period, power-off, and WOL again.
