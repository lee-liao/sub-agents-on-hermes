# Hermes Docker Setup

This workspace runs a single Hermes container for Linux user `lee`.

## Goal

- Keep one persistent Hermes home for `lee`
- Run Hermes gateway in Docker
- Allow interactive CLI and shell access against the same Hermes state
- Mount `lee`'s wiki into the container

## Final Compose File

File: `docker-compose.yml`

```yaml
services:
  hermes:
    image: nousresearch/hermes-agent:latest
    container_name: hermes-lee
    restart: unless-stopped
    environment:
      HERMES_UID: "1000"
      HERMES_GID: "1000"
      PATH: /opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
      VIRTUAL_ENV: /opt/hermes/.venv
    volumes:
      - /home/lee/.hermes:/opt/data
      - /home/lee/wiki:/opt/data/wiki
    command: ["gateway", "run"]
    stdin_open: true
    tty: true
```

## Why This Configuration

- `command: ["gateway", "run"]` starts Hermes in gateway mode as the main container process.
- `/home/lee/.hermes:/opt/data` keeps Hermes sessions, memories, skills, config, and other persistent state on the host.
- `/home/lee/wiki:/opt/data/wiki` exposes the host wiki inside the Hermes home tree.
- `HERMES_UID` and `HERMES_GID` let the image remap its internal `hermes` user to match host user `lee` instead of forcing Docker `user:` directly.
- `PATH` and `VIRTUAL_ENV` make the Hermes CLI available from `docker exec` without manually activating the Python virtualenv.

## What Was Fixed

The original attempt used:

```yaml
command: sleep infinity
```

That did not work because the image has its own entrypoint, and Docker passed `sleep infinity` as arguments to the Hermes CLI. The container restarted because Hermes treated `sleep` as an invalid subcommand.

Using `entrypoint: ["sleep", "infinity"]` worked for shell access, but it disabled normal Hermes startup. The final setup keeps Hermes running normally in gateway mode instead.

## Verified Behavior

These were verified successfully:

- `docker compose up -d` starts the container
- `docker ps --filter name=hermes-lee` shows the container as `Up`
- `docker exec hermes-lee hermes status` works
- `docker exec -it -u hermes hermes-lee /bin/bash` works
- `/opt/data/wiki` exists inside the container and is bind-mounted from `/home/lee/wiki`

## Daily Commands

Start or update the service:

```bash
docker compose up -d
```

Open a shell:

```bash
docker exec -it -u hermes hermes-lee /bin/bash
```

Run Hermes CLI:

```bash
docker exec -it -u hermes hermes-lee hermes chat
docker exec -u hermes hermes-lee hermes status
docker exec -it -u hermes hermes-lee hermes setup
docker exec -u hermes hermes-lee hermes doctor
docker exec -u hermes hermes-lee hermes doctor --fix
```

Check logs:

```bash
docker logs hermes-lee
```

## Notes

- Hermes gateway currently starts in the container, but messaging platforms still need to be configured before it becomes useful.
- Hermes CLI and gateway share the same persistent state because both use `/opt/data` backed by `/home/lee/.hermes`.
- If more Linux users need Hermes later, run separate containers with separate host directories for their Hermes homes.
