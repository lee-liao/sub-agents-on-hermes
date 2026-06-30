# sub-agents-on-hermes

Run a [Hermes](https://nousresearch.com) agent in Docker and let it orchestrate
Claude Code **GOLD sessions** — the `golden_session` engine drives the headless
`claude -p` CLI with the *prime once → fork per task → resume to recover* pattern,
with the Phase 1 contract enforced in code rather than improvised by an LLM.

- Hermes runs as a persistent gateway container with its home on a host bind mount.
- `golden_session` is a zero-dependency (stdlib-only) Python engine the agent calls.
- See [`golden_session/README.md`](golden_session/README.md) for the engine internals.

## Quick start

### 1. Start the Hermes agent (Docker Compose)

```bash
cp .env.example .env      # fill in ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL / API_TIMEOUT_MS
docker compose up -d      # starts the hermes-lee gateway container
docker ps --filter name=hermes-lee   # should show "Up"
```

Open a shell or the Hermes CLI inside the running container:

```bash
docker exec -it -u hermes hermes-lee /bin/bash   # shell
docker exec -it -u hermes hermes-lee hermes       # interactive Hermes CLI
```

### 2. Configure & diagnose Hermes settings

```bash
docker exec -u hermes -it hermes-lee hermes setup    # guided first-time configuration
docker exec -u hermes hermes-lee hermes status        # current state
docker exec -u hermes hermes-lee hermes doctor        # diagnose settings/health
docker exec -u hermes hermes-lee hermes doctor --fix  # auto-fix common issues
docker logs hermes-lee                                # container logs
```

## Documentation

Read further in the [`docs/`](docs/) folder:

- [`docs/DOCKER_COMPOSE_SETTINGS.md`](docs/DOCKER_COMPOSE_SETTINGS.md) — why each setting in `docker-compose.yml` matters (volumes, `HERMES_UID`/`GID` permissions, how `claude` gets its key).
- [`docs/HERMES_DEPLOY_AND_TEST.md`](docs/HERMES_DEPLOY_AND_TEST.md) — full runbook to deploy and verify the `golden_session` engine.
- [`docs/HERMES_HOME_AND_OS_HOME.md`](docs/HERMES_HOME_AND_OS_HOME.md) — how `HERMES_HOME` and OS `$HOME` interact (and why it matters).
- [`docs/OUTPUT_ISOLATION.md`](docs/OUTPUT_ISOLATION.md) — enforced per-task output isolation (`--run-dir` / `GS_RUN_DIR` + the confine-writes hook).
- [`docs/prd/`](docs/prd/) — product requirements, design decisions, and open threads.
