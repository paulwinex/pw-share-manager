# NAS Manager

Manage Samba shares through a REST API: users → groups → shares, synchronized with
the Samba registry (`net conf`), with auto-expiry of temporary access.

Everything runs in a single Docker container (`nas-app`): Samba (`smbd`) + a FastAPI app.
Nothing needs to be installed on the host — only Docker.

## Features

- **Users** — create/delete, change password, admin rights. Actually creates an OS user
  and a Samba account (`useradd` + `smbpasswd`).
- **Groups** — every user gets a personal group (RW, cannot be deleted).
- **Shares** — registered in the Samba registry and exported from a directory inside
  the share root (the directory must already exist on the server). Each share has a
  **Wide symlinks** option (Samba `wide links`): off by default, which is what you want
  unless symlinks in the share point outside it.
- **Access** — group members get the group's shares. Permissions: `rw` / `ro`.
  If a user is in several groups, rights combine (RW wins).
- **Temporary access** — a member can have an `expires_at`; a background scheduler
  (APScheduler) revokes expired access and re-syncs Samba.
- **Sync engine** — compares the desired state (database) with the Samba registry and
  applies the diff (`valid users` / `write list` / `read list` change on the fly).
- **Mount script** — an endpoint returns ready-to-use mount commands
  (Windows `net use` / Linux `mount -t cifs`) for a specific user.

## Self-Service

### Web interface
- `/` — "My shares": list of shares available to you, download button for the mount-share.py script, manual connection.
- `/profile` — profile, change password.
- `/admin` — admin panel (admin accounts only).

### CLI `nasmount` (mount-share.py)
A single-file cross-platform script for mounting shares from this server.
Requires only Python 3 (stdlib), no dependencies; works on Linux, macOS and Windows.

The `app/mount_script/mount-share.py` file is downloaded from the "My shares"
page with the "Download script" button.

**Quick start:**
```bash
python3 mount-share.py setup     # interactive wizard: configure, log in, and mount
python3 mount-share.py auth      # log in; tokens stored in ~/.config/nasmanager/config.json
python3 mount-share.py mount     # mount all shares (sudo on Linux/macOS)
python3 mount-share.py umount    # unmount shares from this NAS
python3 mount-share.py status    # shares from server + mount status
python3 mount-share.py config    # show config; set --root PATH / --url URL
```

The `setup` command is an all-in-one wizard. It asks, in order, for the server URL,
username and password, logs in, prompts for the default mount root, then lists the
available shares and offers to mount them all:

```text
$ python3 mount-share.py setup
Server URL (default http://nas:8000): http://nas:8000
Username: alice
Password:
Mount root path (default /mnt/nas): /mnt/nas
Available shares:
Share                    Access   Host
------------------------------------------------------------
photos                   rw       nas:445
temp                     ro       nas:445
------------------------------------------------------------
Mount all 2 shares? [Y/n]: y
Password for alice@nas:
mounted photos -> /mnt/nas/photos
mounted temp -> /mnt/nas/temp
```

Answer `n` (or anything other than `y`/`yes`/empty) at the final prompt to save the
configuration without mounting anything.

`mount` and `umount` accept share names, e.g. `python3 mount-share.py mount photos`.
Default server address: `http://nas:8000` (change with the `config --url` command).

## Running

Requires Docker (Linux). Samba ports `445/139` are not published to the host — the
container lives in its own docker network and clients connect to the hostname `nas`.

### 1. Configuration

Copy the example and edit if needed:

```bash
cp .env.example .env
```

Variables:

| Variable | Default | Description |
|---|---|---|
| `SHARE_HOST_PATH` | `./share` | host path where share directories live |
| `SHARE_MOUNT_PATH` | `/mnt/share` | share root inside the container |
| `DB_PATH` | `/data/app.db` | SQLite file inside the container (volume `./data`) |
| `SAMBA_SERVICE_USER` | `service-user` | system user that serves the shares |
| `WORKGROUP` | `WORKGROUP` | Samba workgroup |
| `JWT_SECRET` | `change-me-in-dev` | **must be changed** to a secret |
| `JWT_TTL_MINUTES` | `60` | token lifetime |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `admin123` | first admin (created on first start) |
| `EXPIRY_CHECK_INTERVAL_SECONDS` | `60` | how often expired temporary access is checked |

### 2. Start

```bash
just up          # docker compose build + up -d
```

Or without `just`:

```bash
docker compose -f deploy/compose.yml --project-directory . up -d --build
```

After startup the API is available at `http://localhost:8000`, interactive docs at
`http://localhost:8000/docs`. Health check: `http://localhost:8000/api/v1/health`.

> Shares served over Samba are reached by the hostname `nas` inside the docker network
> (e.g. `//nas/photos`). Samba is not reachable outside that network.

### Web UI

- Admin panel: Vue 3 + Quasar 2 in `web-ui/`.
- Dev: `just ui-dev` — dev server, `/api` is proxied to `http://localhost:8087`
  (the local `APP_PORT`; override with `API_PROXY_URL`), no CORS needed.
- Type-check: `just ui-typecheck`. Build: `just ui-build` (output in `web-ui/dist/spa`).
- Prod: multi-stage build — the UI is built and served by FastAPI at `/`
  (port 8000). If `ui-dist` is absent from the image, the UI is not mounted.
- Login: `http://localhost:8000` → Sign in (admin/admin123). `/docs` — Swagger as before.

## External port and Samba access

`docker compose` publishes only the **HTTP port 8000** (API and `/docs`) to the host.
Samba ports **445** (SMB) and **139** (NetBIOS) are not forwarded out of the container:
they are usually already taken by the host `smbd`, and forwarding would create a conflict.

So shares are mounted from a client machine as follows:

- **client on the same docker network**: by the hostname `nas` (e.g. `//nas/photos`);
  the container name resolves via docker's DNS in the `nas` network;
- **Windows host and other machines**: cannot reach the container directly until Samba
  is exposed. Options:
  - publish the ports on the host (`445/139`) and use the Docker host's address —
    the host's Samba must be stopped/reconfigured to free those ports;
  - mount the shares on the NAS host itself (`mount -t cifs //nas/...` in its
    docker network) and re-share them with the host's own Samba/NFS;
  - set up a separate externally visible service (reverse-proxying 445 is non-standard
    for SMB and not recommended).

For development the "client on the docker network" path is handy
(`just client-up`, `just integration`).

## API

All endpoints except `/auth/login`, `/auth/token` and `/health` require an
`Authorization: Bearer <token>` header. Tokens are issued to admins.

Swagger UI (`http://localhost:8000/docs`) has an **Authorize** button: enter the
admin's **username and password**, and Swagger will request a token itself via
`POST /api/v1/auth/token` and attach it to every request.

To log in programmatically, either works (both return the same JWT):

```bash
# JSON (request body)
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'

# form (same as the Authorize button)
curl -s -X POST http://localhost:8000/api/v1/auth/token \
  -d 'username=admin&password=admin123'
```

The full endpoint list and interactive examples live in Swagger UI at
`http://localhost:8000/docs`.

Changes are applied to Samba automatically after every change; inspect the result
with: `docker exec nas-app net conf showshare photos`.

## Mounting a share on a client

Samba listens only in the docker network, hostname `nas`. Windows:

```text
net use Z: \\nas\photos /user:alice alicepw
```

Linux:

```bash
mount -t cifs //nas/photos /mnt/photos -o username=alice,password=alicepw
```

Ready-to-use commands per user are returned by the mount-script endpoint:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/token \
  -d 'username=admin&password=admin123' | jq -r .access_token)
curl -s http://localhost:8000/api/v1/users/alice/mount-script \
  -H "Authorization: Bearer $TOKEN"
```

## Tests

Unit tests (mock OS/Samba calls, run in a one-off container — own compose, no shared
network):

```bash
just test
```

Integration tests (require a running `nas-app` and a client container):

```bash
just client-up pc1      # create a client
just client-setup pc1   # install smbclient/cifs-utils
just integration pc1    # full E2E: rw/ro rights, access expiry, mount-script
```

The integration creates temporary objects with a unique suffix and cleans up after
itself (including via `trap` on failure).

## Common commands (`just`)

| Command | What it does |
|---|---|
| `just up` | build and start |
| `just down` | stop |
| `just build` | rebuild the image |
| `just restart` | restart the container (code is picked up via bind-mount) |
| `just logs` | application logs |
| `just test` | unit tests |
| `just integration pc1` | integration tests |
| `just smb-list pc1 alice alicepw` | list shares as seen by a user |
| `just mount-share pc1 photos alice alicepw /mnt/photos` | mount a share inside the client |
| `just smoke-registry` | list shares in the Samba registry |
