# `docker-compose.yml` settings explained

> **Audience: anyone standing up or adapting `hermes-lee`.** This walks through
> *why* each non-obvious setting in [`docker-compose.yml`](../docker-compose.yml)
> is there. The bigger architectural lessons live in
> [`HERMES_HOME_AND_OS_HOME.md`](./HERMES_HOME_AND_OS_HOME.md); deploy steps live
> in [`HERMES_DEPLOY_AND_TEST.md`](./HERMES_DEPLOY_AND_TEST.md).

For reference, the service:

```yaml
services:
  hermes:
    image: nousresearch/hermes-agent:latest
    container_name: hermes-lee
    restart: unless-stopped
    environment:
      HERMES_UID: "1001"
      HERMES_GID: "1001"
      ANTHROPIC_AUTH_TOKEN: "${ANTHROPIC_AUTH_TOKEN}"
      ANTHROPIC_BASE_URL: "${ANTHROPIC_BASE_URL}"
      _HERMES_FORCE_ANTHROPIC_BASE_URL: "${ANTHROPIC_BASE_URL}"
      API_TIMEOUT_MS: "${API_TIMEOUT_MS}"
      PATH: /opt/hermes/.venv/bin:/opt/data/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
      VIRTUAL_ENV: /opt/hermes/.venv
    volumes:
      - /home/lee/.hermes:/opt/data
      - /home/lee/wiki:/opt/data/wiki
      - /home/lee/projects:/opt/data/projects
      - /home/lee/hermes-docker-lee:/opt/data/hermes-docker-lee:ro
    command: ["gateway", "run"]
    stdin_open: true
    tty: true
```

---

## `volumes:` — why these mappings exist

Hermes keeps **all** of its state under one home directory, which the image
fixes at `/opt/data` inside the container. The bind mounts put that state — and
the data the agent works on — on the host filesystem so it survives container
recreations and image upgrades, and so a human can read/edit it from outside.

| Mount | Purpose |
|---|---|
| `/home/lee/.hermes:/opt/data` | **The Hermes home.** `config.yaml`, `sessions/`, `registry.json`, the OS-tool dotdirs (`.claude/`, `.npmrc`, `.local/`), and the installed `golden_session` package all live here. Bind-mounting it means *nothing* important is trapped inside the container layer — `docker compose down` + image upgrade loses nothing. |
| `/home/lee/wiki:/opt/data/wiki` | Exposes the host wiki inside the Hermes home tree so the agent can read/write it. |
| `/home/lee/projects:/opt/data/projects` | The working directories the agent runs `claude` against (read-write). |
| `/home/lee/hermes-docker-lee:/opt/data/hermes-docker-lee:ro` | **This repo**, mounted **read-only** (`:ro`). The agent can read the runbook and the `golden_session` source it deploys, but cannot write back over the source of truth. |

The nested mounts (`/opt/data/wiki`, `/opt/data/projects`, …) are layered *on
top of* the `/opt/data` home mount — Docker resolves the deeper paths after the
parent, so each subtree is backed by its own host directory.

## `HERMES_UID` / `HERMES_GID` — file ownership in *and* out of the container

Inside the image, Hermes runs as a non-root user named `hermes`. By default that
user has some baked-in UID/GID. The problem: a bind mount is just the host
directory shown inside the container — **file ownership is by numeric UID/GID,
not by username**. If the container writes files as UID 999 but the host owner
`lee` is UID 1001, every file Hermes creates on `/home/lee/.hermes` shows up on
the host owned by a stranger, and `lee` can't edit them without `sudo` — and
vice-versa, the container may not be able to read files `lee` created.

`HERMES_UID: "1001"` / `HERMES_GID: "1001"` tell the image's entrypoint to
**remap the internal `hermes` user to UID/GID 1001**, matching host user `lee`.
Result:

- Files the container writes to the bind mounts are owned by `lee` on the host —
  editable both ways, no `sudo`, no `chown` dance.
- The numbers **must match the host owner** of `/home/lee/*`. Check with
  `id lee` on the host; if `lee` is not `1001`, change both values to match.

This is preferred over Docker's `user:` directive because the image's entrypoint
needs to start as root to fix permissions and remap, then drop to the `hermes`
user — forcing `user:` would skip that setup.

## How the Claude Code CLI gets its API key

Authentication flows through **environment variables**, not dotfiles — which is
why it keeps working even across the home-dir drift described in
[`HERMES_HOME_AND_OS_HOME.md`](./HERMES_HOME_AND_OS_HOME.md).

1. **Secrets come from `.env`, never the repo.** `ANTHROPIC_AUTH_TOKEN`,
   `ANTHROPIC_BASE_URL`, and `API_TIMEOUT_MS` are `${...}` references that
   Compose substitutes from the host `.env` file (gitignored; see
   [`.env.example`](../.env.example)). The compose file holds no secrets.
2. **The agent spawns `claude` via Hermes' `terminal()` tool**, which starts a
   subprocess. That subprocess needs to inherit the auth env vars to reach the
   API.
3. **The blocklist gotcha.** Hermes has a security blocklist
   (`tools/environments/local.py`) that **strips `ANTHROPIC_BASE_URL`** from
   terminal subprocess envs — but does *not* strip `ANTHROPIC_AUTH_TOKEN`. So
   `claude` inherits a token but no endpoint → 401 *"Invalid bearer token"*. The
   `env_passthrough` config **cannot** override this (GHSA-rhgp-j443-p4rf).
4. **The fix: `_HERMES_FORCE_ANTHROPIC_BASE_URL`.** Hermes' supported escape
   hatch is the `_HERMES_FORCE_` prefix — it unblocks the variable and renames
   it back, so the subprocess sees `ANTHROPIC_BASE_URL`. Setting
   `_HERMES_FORCE_ANTHROPIC_BASE_URL: "${ANTHROPIC_BASE_URL}"` is the root-cause
   fix. **Changing it requires `docker compose up -d --force-recreate hermes`.**
   (A secondary fix — the `bin/golden_session` shim re-exporting the base URL —
   covers only `golden_session` calls and is documented in that script.)

So the end-to-end path is: host `.env` → Compose substitution → container env →
`_HERMES_FORCE_` un-blocks the base URL → Hermes `terminal()` subprocess → `claude -p`.

## The other settings, briefly

- **`command: ["gateway", "run"]`** — starts Hermes in gateway mode as the main
  container process (PID 1's child). Don't use `command: sleep infinity`: the
  image has its own entrypoint and would pass `sleep` as a Hermes subcommand,
  which fails and restart-loops.
- **`PATH` / `VIRTUAL_ENV`** — make the Hermes CLI (in `/opt/hermes/.venv`) and
  the deployed `golden_session` shim (in `/opt/data/.local/bin`) runnable from
  `docker exec` without manually activating the virtualenv.
- **`restart: unless-stopped`** — the gateway comes back after a host reboot or
  crash, but stays down if you deliberately `docker compose stop` it.
- **`stdin_open` + `tty`** — keep an interactive TTY so `docker exec -it … hermes`
  works as a real interactive CLI.

> **Why not `sleep infinity`?** An early attempt used `command: sleep infinity`,
> which failed: the image has its own entrypoint, so Docker passed `sleep infinity`
> as *arguments to the Hermes CLI*, Hermes treated `sleep` as an invalid
> subcommand, and the container restart-looped. Overriding `entrypoint: ["sleep",
> "infinity"]` gave shell access but disabled normal Hermes startup. The current
> `command: ["gateway", "run"]` keeps Hermes running normally instead.

## Verifying it works

After `docker compose up -d`, these should all succeed:

```bash
docker ps --filter name=hermes-lee                       # shows the container "Up"
docker exec hermes-lee hermes status                     # Hermes responds
docker exec -it -u hermes hermes-lee /bin/bash           # shell opens
docker exec hermes-lee ls /opt/data/wiki                 # bind mount present inside
```

## Notes

- **Gateway ≠ usable yet.** `gateway run` starts Hermes, but messaging platforms
  (Discord/IM, etc.) still need to be configured before it does anything useful —
  run `hermes setup`.
- **CLI and gateway share one state.** Both use `/opt/data` backed by host
  `/home/lee/.hermes`, so `docker exec … hermes` and the running gateway see the
  same sessions, config, and registry.
- **More users → more containers.** If another Linux user needs Hermes later, run
  a *separate* container with its own host directory for that user's Hermes home
  and its own matching `HERMES_UID`/`HERMES_GID`. Don't share one home across users.
