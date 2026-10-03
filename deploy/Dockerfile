# --- UI build stage ---
FROM node:22-alpine AS ui-build
WORKDIR /app/ui
COPY web-ui/package.json web-ui/yarn.lock ./
RUN yarn install --frozen-lockfile --ignore-scripts
COPY web-ui ./
RUN yarn build

# --- Python runtime ---
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# samba-vfs-modules ships vfs_widelinks, which a share with
# "wide links = yes" loads. Without it that share fails to open with
# NT_STATUS_BAD_NETWORK_NAME, so the per-share option would break the share.
RUN apt-get update && apt-get install -y --no-install-recommends \
        samba smbclient samba-vfs-modules python3 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --all-extras

ENV PATH="/app/.venv/bin:$PATH" \
    UV_NO_SYNC=1

COPY app ./app
COPY deploy/smb.conf.template ./deploy/smb.conf.template
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --from=ui-build /app/ui/dist/spa ./ui-dist
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /mnt/share /data /var/lib/samba/private /var/log/samba

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
