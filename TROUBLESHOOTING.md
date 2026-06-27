# Hermes Docker Troubleshooting

Lessons from resolving `hermes doctor` warnings in this workspace.

## Issue 1: "Add ~/.local/bin to your PATH"

### Symptom

`hermes doctor` reported:

```
3. Add ~/.local/bin to your PATH
```

### Investigation

Inside the container:

- `$HOME` is `/opt/data` (the bind-mount of `/home/lee/.hermes`)
- So `~/.local/bin` resolves to `/opt/data/.local/bin`
- The `PATH` env var set in `docker-compose.yml` did not include that directory

Doctor only emits this warning when run with `--fix`. The fix creates a symlink at `/opt/data/.local/bin/hermes`, then checks whether that directory is in `$PATH`.

### Fix

Edit `docker-compose.yml` and add `/opt/data/.local/bin` to the `PATH` env var:

```yaml
environment:
  PATH: /opt/hermes/.venv/bin:/opt/data/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
```

Then recreate the container so the new env takes effect:

```bash
docker compose up -d
```

A plain restart is not enough — env changes require recreating the container.

Finally, run doctor with `--fix` as the hermes user to create the symlink:

```bash
docker exec -u hermes hermes-lee hermes doctor --fix
```

## Issue 2: Always run `docker exec` as the hermes user

### Symptom

After running `docker exec hermes-lee hermes doctor` (without `-u hermes`), files under `/home/lee/.hermes` became owned by `root`. Subsequent runs as hermes failed with:

```
PermissionError: [Errno 13] Permission denied: '/opt/data/memories/MEMORY.md'
```

### Why this happens

Without `-u`, `docker exec` runs as the image's default user, which is `root` for this image. The entrypoint's privilege drop to `hermes` does not apply to `docker exec`. So files created or modified during that session are owned by `root` (uid 0), not `hermes` (uid 1001).

### Fix

Always use `-u hermes`:

```bash
docker exec -u hermes hermes-lee hermes doctor
docker exec -u hermes hermes-lee hermes setup
docker exec -it -u hermes hermes-lee /bin/bash
```

### Recovering from broken permissions

If you've already run as root and polluted ownership, fix it from the host (the bind-mount maps `/home/lee/.hermes` 1:1 to `/opt/data`):

```bash
sudo chown -R lee:lee /home/lee/.hermes
find /home/lee/.hermes -user root | wc -l   # should print 0
```

UID 1001 on the host (`lee`) is the same UID 1001 inside the container (`hermes`), so the fix is symmetric across the bind-mount.

## Issue 3: `--fix` ran as root creates the symlink in the wrong place

If you ran `docker exec hermes-lee hermes doctor --fix` (no `-u`) earlier, doctor created the symlink at `/root/.local/bin/hermes`, not `/opt/data/.local/bin/hermes`. Re-running `--fix` as hermes creates it in the right location.

```bash
docker exec -u hermes hermes-lee hermes doctor --fix
```

## Remaining doctor warnings (not path-related)

- `ui-tui workspace has 1 npm vulnerability` — needs `npm audit fix` in the ui-tui workspace, not covered here.
- `Run 'hermes setup' to configure missing API keys` — needs `hermes setup` to configure provider keys.

## Key takeaways

1. Set `PATH` in `docker-compose.yml` so it survives container recreation; include `/opt/data/.local/bin` so pip-installed user binaries work.
2. Always pass `-u hermes` to `docker exec`. The container's default user is root.
3. If permissions get broken, `sudo chown -R lee:lee /home/lee/.hermes` from the host fixes both sides of the bind-mount.
