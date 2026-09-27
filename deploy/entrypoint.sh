#!/usr/bin/env bash
set -euo pipefail

SERVICE_USER="${SAMBA_SERVICE_USER:-service-user}"
MOUNT_ROOT="${SHARE_MOUNT_PATH:-/mnt/share}"
WORKGROUP_NAME="${WORKGROUP:-WORKGROUP}"

id "$SERVICE_USER" >/dev/null 2>&1 || useradd -r -M -s /usr/sbin/nologin "$SERVICE_USER"

sed "s/@WORKGROUP@/${WORKGROUP_NAME}/g" /app/deploy/smb.conf.template > /etc/samba/smb.conf

mkdir -p /var/lib/samba/private /var/log/samba "$MOUNT_ROOT"

#net conf setparm global "socket options" "TCP_NODELAY IPTOS_LOWDELAY SO_RCVBUF=1048576 SO_SNDBUF=1048576"
#net conf setparm global "aio read size" "16384"
#net conf setparm global "aio write size" "16384"
#net conf setparm global "use sendfile" "yes"
#net conf setparm global "min receivefile size" "16384"

#smbcontrol all reload-config
smbd -D || { echo "smbd failed to start"; exit 1; }

exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
