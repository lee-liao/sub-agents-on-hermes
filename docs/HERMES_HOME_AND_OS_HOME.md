# HERMES_HOME vs the container OS home — lessons learned

> **Audience: anyone maintaining `hermes-lee` or adapting this setup to another
> host.** This doc explains the architectural conflation that caused a 3-day
> debugging detour during Phase 1 bring-up, and the rule that prevents it
> returning.

## TL;DR

In this container, **`HERMES_HOME` and the OS home for the `hermes` user are the
same directory**: `/opt/data`. That's by image design and is harmless — *as long
as `terminal.home_mode: real`*. The image default (`auto`) forks the agent's
terminal subprocesses into a fake sibling home at `/opt/data/home`, drifts them
away from the real credential dotdirs, and creates a parallel orphan install
set. Set `terminal.home_mode: real` once per host (Phase 0 of the runbook).

## The two "homes" defined

| Concept | Source of truth | What lives there | In this container |
|---|---|---|---|
| **HERMES_HOME** | `HERMES_HOME` env var, defaults to `~/.hermes` on host | Hermes Agent state: `config.yaml`, `sessions/`, `registry.json`, gateway/agent runtime | `/opt/data` (bind-mounted from host `/home/lee/.hermes`) |
| **OS home** | `/etc/passwd` for the user | OS-tool dotdirs: `.claude/`, `.claude.json`, `.npmrc`, `.bashrc`, `.profile`, `.local/` | `/opt/data` (`hermes:x:1001:1001::/opt/data:/bin/sh`) |

The image's entrypoint intentionally aligns these — the `hermes` user's OS home
*is* `HERMES_HOME`. Hermes' own files (`config.yaml`, `sessions/`) coexist with
OS-tool dotfiles (`.claude/`, `.npmrc/`) under the same root. No collisions;
same bind mount; same persistence story.

## Why `home_mode` exists

Hermes' `terminal()` tool spawns subprocesses for the agent (run bash, run
`claude`, etc.). The question: what should `HOME=` be for *those subprocesses*?

- `auto` (image default): **host** installs keep the real OS home; **containers**
  redirect to `{HERMES_HOME}/home` so the container's tool dotdirs don't pollute
  the host-scoped `HERMES_HOME` root.
- `real`: always use the OS home from `/etc/passwd`.
- `profile`: force `{HERMES_HOME}/home` even on hosts (strict per-profile
  isolation; legacy).

The `auto` rule has sound reasoning for the general case (you don't want a
container's `.npm` cache landing next to your laptop's `config.yaml`). But in
**this** deployment, `/opt/data` is already container-scoped — there's no host
to protect. So `auto`'s "redirect to /opt/data/home" rule creates a third,
fake home that nothing else looks at, and the agent's subprocesses drift.

## The drift we hit (2026-06-28 → 2026-06-29)

With `home_mode: auto`, the gateway (PID 1's child) saw `HOME=/opt/data`, but
the agent's terminal subprocesses saw `HOME=/opt/data/home`. Two parallel sets
of dotdirs emerged:

| Real (gateway, `docker exec`) | Drift (agent subprocesses) |
|---|---|
| `/opt/data/.claude/` (Jun 19, original) | `/opt/data/home/.claude/` (Jun 28, fake) |
| `/opt/data/.claude.json` | `/opt/data/home/.claude.json` |
| `/opt/data/.npmrc` → `prefix=/opt/data/.local` | `/opt/data/home/.npmrc` → `prefix=/opt/data/home/.npm-global` |
| `/opt/data/.local/bin/claude` (the keeper) | `/opt/data/home/.npm-global/bin/claude` (orphan, 234 MB) |

`claude auth status` returned `loggedIn: true` either way, because auth flows
through env vars (`ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL`), not dotfiles.
That masked the drift for a while — sessions "worked," but `userID`/`machineID`
diverged between paths, npm installs landed in different prefixes, and
debugging produced contradictory answers depending on which shell ran the
probe.

## Why an env-var override doesn't work

Hermes *does* bridge `terminal.home_mode` ↔ `TERMINAL_HOME_MODE` env var (see
`hermes_constants.py:377` for the reader). So the obvious "set
`TERMINAL_HOME_MODE=real` in compose `environment:`" looks like it should work.
**It does not.** The gateway's config bridge in `gateway/run.py:1261–1273`
unconditionally overwrites the env var from `config.yaml` for every `terminal.*`
key:

```python
for _cfg_key, _env_var in _terminal_env_map.items():
    if _cfg_key in _terminal_cfg:        # if key is in config.yaml…
        _val = _terminal_cfg[_cfg_key]
        …
        os.environ[_env_var] = str(_val) # …always overwrite the env var
```

The comment a few lines above is explicit: *"config.yaml overrides .env for
these since it's the documented config path."* The CLI's bridge in `cli.py:634`
has a `if _file_has_terminal_config or env_var not in os.environ` guard, but
the gateway's does not. Since this deployment runs as a gateway, config.yaml is
authoritative for `terminal.*`.

The same logic rules out trying to "delete `home_mode` from config.yaml and set
it only via env var" — `hermes config migrate` on the next image upgrade will
re-add the default, and the env var will be silently clobbered again.

## The fix

Use Hermes' supported entry point to write the value into `config.yaml` itself:

```bash
hermes config set terminal.home_mode real
```

Idempotent. One line. Lands in `~/.hermes/config.yaml` on the bind mount,
survives container recreations and `hermes config migrate`. Run after
`hermes setup`, before the first gateway start. See Phase 0 of the runbook.

## Operational rules (the takeaways)

1. **One home, by design.** Treat `/opt/data` as both HERMES_HOME and the
   `hermes` OS home. Don't try to separate them — the image fights back, and
   the conflation is harmless once `home_mode: real`.
2. **Never trust `$HOME` reported by a single shell.** `docker exec` shows the
   gateway env; agent-spawned subprocesses may differ. If you see two different
   `$HOME` values for the "same" user, suspect `home_mode` first.
3. **Auth via env vars masks dotdir drift.** `claude auth status: loggedIn`
   doesn't prove the right dotdir is being read — only that the env vars are
   set. Always also check `which claude`, the `.claude.json` `machineID`, and
   the npm prefix if you suspect drift.
4. **`terminal.*` lives in `config.yaml`, not env vars.** Don't try to set
   terminal settings via compose `environment:` — the gateway bridge will
   overwrite them. Use `hermes config set`.
5. **Image upgrades run `hermes config migrate`.** User-set values are
   preserved across schema bumps; defaults like `home_mode: auto` are restored
   only if the key is absent. So once `home_mode: real` is set, it stays.

## Artifacts from this debugging session

- `~/.hermes/config.yaml` line 35: `home_mode: real` (the fix)
- `docs/HERMES_DEPLOY_AND_TEST.md` Phase 0 (operational recipe) + Troubleshooting
  row (drift symptom)
- `/opt/data/.claude.bak-20260629T213558Z` — pre-migration backup of the
  original `.claude*`; safe to delete after a few clean Phase 1 runs
- `~/.claude/projects/-home-lee-hermes-docker-lee/memory/project_home_mode_decision.md`
  — short-form memory pointer for future sessions
