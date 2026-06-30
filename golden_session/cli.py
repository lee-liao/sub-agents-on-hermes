"""`golden_session` command-line interface.

Thin argv layer over the engine + registry. The Hermes agent (or an operator)
calls these subcommands; identity is resolved in code from the registry so the
caller only ever supplies a human-readable name (F11 / Decision A2).

    golden_session prime   --name N --cwd PATH --context-file F   # once, writes registry
    golden_session run     --name N --task "..."                  # fork a task (F2)
    golden_session continue --name N --session-id SID --task "..."  # recover (F4) [direct/automation]
    golden_session list                                          # discovery (F11)
    golden_session cleanup --name N --keep SID                   # manual janitor
    golden_session remove  --name N                              # drop a registry entry
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from typing import Optional, Sequence

from .errors import GoldenSessionError, RegistryError
from .registry import Registry, ResolvedRun
from .runner import ClaudeRunner, default_runner
from .session import GoldenSession


def main(argv: Optional[Sequence[str]] = None, *, runner: ClaudeRunner = default_runner) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)
    if not getattr(ns, "func", None):
        parser.print_help()
        return 2
    registry = Registry(ns.registry)
    try:
        return ns.func(ns, registry, runner)
    except RegistryError as exc:
        _emit_error(exc, hints=exc.known_names)
        return 2
    except GoldenSessionError as exc:
        _emit_error(exc)
        return 2


# --- subcommand handlers -------------------------------------------------


def _cmd_prime(ns: argparse.Namespace, registry: Registry, runner: ClaudeRunner) -> int:
    # Reserve the name first so we never prime a GOLD for a name that already exists.
    if ns.name in registry.names():
        raise RegistryError(
            f"session name {ns.name!r} already exists — priming twice is forbidden (F1/F7)",
            known_names=registry.names(),
        )
    context = _read_context(ns)
    golden_id = ns.golden_id or str(uuid.uuid4())

    max_turns = ns.max_turns
    max_budget = ns.max_budget_usd
    ceiling_turns = ns.ceiling_turns if ns.ceiling_turns is not None else max_turns
    ceiling_budget = ns.ceiling_budget if ns.ceiling_budget is not None else max_budget

    gs = GoldenSession(
        workspace=ns.cwd,
        golden_id=golden_id,
        max_turns=max_turns,
        max_budget_usd=max_budget,
        max_continues=ns.max_continues,
        allowed_tools=ns.tools,
        model=ns.model,
        runner=runner,
    )
    result = gs.prime(context)

    entry = registry.add(
        ns.name,
        golden_id=golden_id,
        cwd=ns.cwd,
        description=ns.description or "",
        defaults={
            "allowed_tools": ns.tools or [],
            "max_turns": max_turns,
            "max_budget_usd": max_budget,
            "max_continues": ns.max_continues,
            "model": ns.model,
        },
        ceilings={"max_turns": ceiling_turns, "max_budget_usd": ceiling_budget},
    )
    _emit(
        {
            "ok": True,
            "command": "prime",
            "name": entry.name,
            "golden_id": golden_id,
            "cwd": entry.cwd,
            "prime_cost_usd": result.cost_usd,
        }
    )
    return 0


def _cmd_run(ns: argparse.Namespace, registry: Registry, runner: ClaudeRunner) -> int:
    resolved = registry.resolve(ns.name, _overrides(ns))
    gs = _session(resolved, runner)
    result = gs.run_task(resolved_task(ns), model=resolved.model, run_dir=ns.run_dir)
    _emit(
        {
            "ok": not result.is_error,
            "command": "run",
            "name": ns.name,
            "run_dir": ns.run_dir,
            **result.to_dict(),
        }
    )
    return 0 if not result.is_error else 1


def _cmd_continue(ns: argparse.Namespace, registry: Registry, runner: ClaudeRunner) -> int:
    resolved = registry.resolve(ns.name, _overrides(ns))
    gs = _session(resolved, runner)
    result = gs.continue_task(
        ns.session_id, resolved_task(ns), fork=ns.fork, model=resolved.model, run_dir=ns.run_dir
    )
    _emit(
        {
            "ok": not result.is_error,
            "command": "continue",
            "name": ns.name,
            "forked": ns.fork,
            "run_dir": ns.run_dir,
            **result.to_dict(),
        }
    )
    return 0 if not result.is_error else 1


def _cmd_list(ns: argparse.Namespace, registry: Registry, runner: ClaudeRunner) -> int:
    entries = [
        {
            "name": e.name,
            "cwd": e.cwd,
            "description": e.description,
            "required_args": e.required_args,
            "optional_args": e.optional_args,
            "ceilings": e.ceilings,
        }
        for e in registry.list_entries()
    ]
    if ns.json:
        _emit({"ok": True, "command": "list", "sessions": entries})
    else:
        if not entries:
            print("No sessions registered. Prime one with `golden_session prime`.")
        for e in entries:
            print(f"• {e['name']}  ({e['cwd']})")
            if e["description"]:
                print(f"    {e['description']}")
            print(f"    required: {', '.join(e['required_args'])}")
            print(f"    optional: {', '.join(e['optional_args'])}  (clamped to {e['ceilings']})")
    return 0


def _cmd_cleanup(ns: argparse.Namespace, registry: Registry, runner: ClaudeRunner) -> int:
    resolved = registry.resolve(ns.name)
    gs = _session(resolved, runner)
    deleted = gs.cleanup_forks(keep=ns.keep or [])
    _emit({"ok": True, "command": "cleanup", "name": ns.name, "deleted": deleted})
    return 0


def _cmd_remove(ns: argparse.Namespace, registry: Registry, runner: ClaudeRunner) -> int:
    registry.remove(ns.name)
    _emit({"ok": True, "command": "remove", "name": ns.name})
    return 0


# --- helpers -------------------------------------------------------------


def _session(resolved: ResolvedRun, runner: ClaudeRunner) -> GoldenSession:
    return GoldenSession(
        workspace=resolved.cwd,
        golden_id=resolved.golden_id,
        max_turns=resolved.max_turns,
        max_budget_usd=resolved.max_budget_usd,
        max_continues=resolved.max_continues,
        allowed_tools=resolved.allowed_tools,
        model=resolved.model,
        runner=runner,
    )


def resolved_task(ns: argparse.Namespace) -> str:
    if not ns.task:
        raise RegistryError("missing required arg: task")
    return ns.task


def _overrides(ns: argparse.Namespace) -> dict:
    return {
        "max_budget_usd": getattr(ns, "budget", None),
        "max_turns": getattr(ns, "turns", None),
        "allowed_tools": getattr(ns, "tools", None),
        "model": getattr(ns, "model", None),
    }


def _read_context(ns: argparse.Namespace) -> str:
    if ns.context_file:
        with open(ns.context_file, "r", encoding="utf-8") as fh:
            return fh.read()
    if ns.context:
        return ns.context
    raise RegistryError("prime requires --context or --context-file")


def _emit(obj: dict) -> None:
    print(json.dumps(obj, indent=2))


def _emit_error(exc: Exception, hints: Optional[Sequence[str]] = None) -> None:
    payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    if hints:
        payload["known_names"] = list(hints)
    print(json.dumps(payload, indent=2), file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="golden_session", description="GOLD session orchestrator (Phase 1).")
    p.add_argument(
        "--registry",
        default=os.environ.get("GOLDEN_SESSION_REGISTRY"),
        help="Path to registry.json (default: $GOLDEN_SESSION_REGISTRY or ~/.golden_session/registry.json).",
    )
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("prime", help="Prime a GOLD session once and register a name (F1).")
    sp.add_argument("--name", required=True)
    sp.add_argument("--cwd", required=True, help="Stable workspace path (F6).")
    sp.add_argument("--context")
    sp.add_argument("--context-file")
    sp.add_argument("--golden-id", help="Override the generated UUID (advanced).")
    sp.add_argument("--description", default="")
    sp.add_argument("--tools", nargs="*", default=None, help="Default allowed tools.")
    sp.add_argument("--model", default=None)
    sp.add_argument("--max-turns", type=int, default=20)
    sp.add_argument("--max-budget-usd", type=float, default=1.0)
    sp.add_argument("--max-continues", type=int, default=3, help="F10 retry ceiling default.")
    sp.add_argument("--ceiling-turns", type=int, default=None, help="Override clamp for turns.")
    sp.add_argument("--ceiling-budget", type=float, default=None, help="Override clamp for budget.")
    sp.set_defaults(func=_cmd_prime)

    sr = sub.add_parser("run", help="Fork a task from a named GOLD (F2).")
    sr.add_argument("--name", required=True)
    sr.add_argument("--task", required=True)
    _add_override_args(sr)
    sr.set_defaults(func=_cmd_run)

    sc = sub.add_parser("continue", help="Recover/branch an existing task (F4); direct/automation only.")
    sc.add_argument("--name", required=True)
    sc.add_argument("--session-id", required=True)
    sc.add_argument("--task", required=True)
    sc.add_argument("--fork", action="store_true", help="Branch a new fork instead of appending.")
    _add_override_args(sc)
    sc.set_defaults(func=_cmd_continue)

    sl = sub.add_parser("list", help="List registered sessions + their args (F11 discovery).")
    sl.add_argument("--json", action="store_true")
    sl.set_defaults(func=_cmd_list)

    scl = sub.add_parser("cleanup", help="Delete forked transcripts except GOLD + --keep.")
    scl.add_argument("--name", required=True)
    scl.add_argument("--keep", nargs="*", default=None)
    scl.set_defaults(func=_cmd_cleanup)

    srm = sub.add_parser("remove", help="Remove a registry entry (does not delete transcripts).")
    srm.add_argument("--name", required=True)
    srm.set_defaults(func=_cmd_remove)

    return p


def _add_override_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--budget", type=float, default=None, help="max_budget_usd override (clamped).")
    sp.add_argument("--turns", type=int, default=None, help="max_turns override (clamped).")
    sp.add_argument("--tools", nargs="*", default=None, help="allowed_tools override.")
    sp.add_argument("--model", default=None, help="model override.")
    sp.add_argument(
        "--run-dir",
        default=None,
        help="Per-task directory (F12). Created and exported as GS_RUN_DIR so a "
        "cwd-level confine-writes hook can enforce output isolation.",
    )


if __name__ == "__main__":  # python -m golden_session.cli
    raise SystemExit(main())
