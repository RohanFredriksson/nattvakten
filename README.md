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

The development container mounts the source code read-only and does not receive the host power-off request directory, so it cannot power off the host. `NATTVAKTEN_POWEROFF_ENABLED` should remain `false` in `.env`.

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
- The container runs with the host `nattvakten` UID and can only write a single request file in `/run/nattvakten`. A host `systemd` path unit watches that file and starts the fixed, root-owned `nattvakten-poweroff.service`. The container cannot reach the host D-Bus, systemd, or Docker, and cannot run arbitrary commands as root.

### 1. Install Docker and host tools

Run on the new Ubuntu server:

```bash
sudo apt update
sudo apt install --yes docker.io docker-compose-v2 ethtool curl
sudo systemctl enable --now docker.service
```

### 2. Enable persistent Wake-on-LAN

In firmware, enable Wake-on-LAN and disable settings that remove standby power from the network adapter, such as ErP, deep sleep, or S5 maximum power savings. Then enable magic-packet wake on the physical Ethernet interface. Replace `eno1` if the server uses a different interface:

```bash
sudo ethtool eno1 | grep -i wake
sudo install -m 644 /opt/nattvakten/deploy/nattvakten-wol@.service /etc/systemd/system/nattvakten-wol@.service
sudo systemctl daemon-reload
sudo systemctl enable --now nattvakten-wol@eno1.service
sudo ethtool eno1 | grep -i wake
```

The final command must report `Wake-on: g`. The service reapplies that setting after every boot, before the host later shuts down.

### 3. Copy the application

Clone the repository on the server. No host Python environment or package installation is needed.

```bash
sudo git clone https://github.com/RohanFredriksson/nattvakten.git /opt/nattvakten
```

### 4. Create the service account and configuration

Generate a token on the server. Save it somewhere secure; clients need this exact value in their `Authorization: Bearer` header. The UID and GID make the API container appear as the restricted `nattvakten` account that owns the host power-off request directory.

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

### 5. Install and start the container service

The systemd unit builds and starts the container. When power-off is enabled, the API container writes a single request file into `/run/nattvakten`; a host path unit watches that file and starts the fixed, root-owned `nattvakten-poweroff.service`. The container cannot run arbitrary commands as root.

```bash
sudo install -m 644 /opt/nattvakten/deploy/nattvakten-tmpfiles.conf /etc/tmpfiles.d/nattvakten.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/nattvakten.conf
sudo install -m 644 /opt/nattvakten/deploy/nattvakten.service /etc/systemd/system/nattvakten.service
sudo install -m 644 /opt/nattvakten/deploy/nattvakten-poweroff.service /etc/systemd/system/nattvakten-poweroff.service
sudo install -m 644 /opt/nattvakten/deploy/nattvakten-poweroff.path /etc/systemd/system/nattvakten-poweroff.path
sudo systemctl daemon-reload
sudo systemctl enable --now nattvakten-poweroff.path
sudo systemctl enable --now nattvakten.service
```

Do not replace the path unit with a broad passwordless `sudo` rule or mount the Docker socket or host D-Bus in the container. The container's only host privilege is creating the request file, which can trigger nothing but the fixed power-off unit.

### 6. Verify the API before enabling power-off

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

### 7. Enable and test real power-off

First validate the host power-off path by writing the request file as the service account. This powers the host off immediately, so run it only when it is safe to do so:

```bash
sudo -u nattvakten touch /run/nattvakten/poweroff.request
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
