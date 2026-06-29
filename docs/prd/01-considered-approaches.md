# Considered Approaches for Hermes → Claude Code Orchestration

## Problem statement

Hermes Agent needs to drive Claude Code CLI programmatically to run non-interactive coding tasks against a stable workspace. Per task:

1. Hermes provides task parameters.
2. Claude Code receives the parameters plus stable project context.
3. Claude Code runs, writes files to an `output/` directory.
4. Hermes monitors to completion and parses the result/status.
5. **Stable context must not be polluted by per-task chatter** — it has to remain reusable for the next task.
6. On failure or decision points, the task must be *continuable* with fixes or chosen decisions, without losing prior progress.

The next document (`02-gold-session-management.md`) describes the architecture that satisfies this. This document compares the orchestration substrates that could implement it.

## The five approaches considered

### 1. tmux + send-keys / pipe-pane
Drive the interactive Claude Code TUI by spawning it inside a tmux session and sending keystrokes via `send-keys`. Capture pane output via `pipe-pane` or `capture-pane`.

Originally explored as a streaming experiment. Works for human-observable sessions but is the wrong tool for programmatic orchestration.

### 2. Headless CLI (`claude -p`)
The `claude` CLI's non-interactive print mode. Takes a prompt as argument or stdin, exits when done, prints a JSON result object to stdout. Flags for output format (`text|json|stream-json`), session control (`--session-id`, `--resume`, `--fork-session`), tool permissions, and budget caps.

### 3. Claude Agent SDK
Official SDKs in Python (`claude-code-sdk`) and TypeScript (`@anthropic-ai/claude-code`) that wrap the CLI. Provide typed async message streams instead of JSON parsing. Same underlying flags and session semantics.

### 4. Hermes claude-code skill
A community-maintained skill (`NousResearch/hermes-agent` repo) that documents and wraps the CLI for Hermes orchestration. Covers both print mode and interactive tmux patterns. Provides reference commands and gotchas for both.

### 5. claude-agent-acp (`@agentclientprotocol/claude-agent-acp`)
An ACP-compatible server powered by the Claude Agent SDK. ACP (Agent Client Protocol) is a JSON-RPC protocol for editor-to-agent communication, used by Zed, Agentic.nvim, and similar clients. Provides typed bidirectional events including tool calls and permission requests.

## Comparison matrix

Ratings reflect fit for **this use case** (programmatic task management with the GOLD pattern), not for editor integration generally.

| Dimension | tmux + send-keys | Headless CLI | Claude Agent SDK | Hermes claude-code skill | claude-agent-acp |
|---|---|---|---|---|---|
| Integration cost for Hermes | High — PTY scraping, dialog handling, "done" detection | **Low** — subprocess + JSON parse | Low-Medium — typed async API | **Lowest** — pre-built wrapper | Medium-High — JSON-RPC client + server lifecycle |
| Streaming mid-task | Weak — `capture-pane` polling, ANSI-laden | Yes — `stream-json --verbose` | Yes — async iterators over typed msgs | Yes (print mode) | **Native** — bidirectional JSON-RPC notifications |
| Structured events | None — raw bytes | Yes — JSON objects per event | Yes — typed message objects | Yes (print mode) | **Yes** — typed ACP messages, including explicit permission requests |
| Decision detection | Brittle — visual cues (`❯` prompt) | Prompt convention + final JSON | Tool-call events + msg inspection | Same as headless | **Native** — explicit permission-request events |
| GOLD pattern fit | Poor — drives flags via keystrokes, loses TUI benefits | **Native** — `--session-id` / `--resume` / `--fork-session` | **Native** — wraps same flags | **Native** — uses same flags | **Poor** — single-conversation protocol, no session-id primitives |
| Concurrency | One tmux session/task (heavy) | Trivial — separate processes | Trivial — async tasks | Supported (parallel-instance docs) | Heavy — one ACP server per task |
| Production maturity | Low — version/theme-fragile | **High** — official, stable | High — official | Medium — community v2.2.0 | Low-Medium — brand new (v0.45.1), official org |
| Operational overhead | Medium — manage tmux sessions | Low — stateless subprocess | Low — linked library | Low — same as headless | High — manage ACP servers, protocol layer |
| Cache reuse across calls | No | Yes (same sid) / no (fork) | Yes (same sid) / no (fork) | Same as CLI | Depends on session mgmt — non-trivial |
| Forked-session cleanup | Manual | Manual (or wrapper) | Manual | Manual | Manual — no native fork concept |

## Recommendation tiers

| Tier | Approaches | Why |
|---|---|---|
| **Best fit** | Headless CLI; Claude Agent SDK | GOLD is native, low overhead, mature, production-tested. SDK trades a small integration cost for typed messages. |
| **Acceptable** | Hermes claude-code skill | Same foundation as headless, less wrapper code to write — but community-maintained, version drift risk. |
| **Only if specific strengths matter** | claude-agent-acp | Choose only if native permission-request events and editor-grade bidirectional streaming outweigh the GOLD-engineering tax. |
| **Do not use for this** | tmux + send-keys | Loses tmux's benefits, adds costs, fragile across versions. |

> Confirmed by the official Hermes claude-code doc: print mode (`claude -p`) is Hermes' own
> **preferred** mode, with JSON output (`session_id`, `total_cost_usd`, `stop_reason`) for
> integration. It documents `--resume`/`--fork-session` but **no golden/primed-session
> pattern** — that reuse layer is supplied by this project's wrapper. See
> [`05-integration-and-deployment.md`](./05-integration-and-deployment.md).

## What the Hermes `/claude-code` skill actually provides (and why we still build `golden_session`)

This section records what Hermes ships out of the box for Claude Code, verified against the
official doc and a live run on this deployment. It matters because our `golden_session`
solution (doc 02) is justified precisely by the gap between what `/claude-code` provides and
what the problem statement above requires.

### What `/claude-code` is

- It is a **Hermes agent skill = a markdown guide loaded into the agent's context**, not a
  tool. **Nothing in it executes on its own.** *(Confirmed live: invoking `/claude-code` with
  no task made the agent run only the guide's "Prerequisites: install + auth" step, then stop
  at the first blocker.)*
- Invocation is **agent-in-the-loop**: the agent reads the guide, then issues the actual work
  itself via Hermes' `terminal(command="claude -p …", workdir=…, timeout=…)`. `/claude-code`
  is **not** a command that takes a shell command as an argument — the slash command only loads
  the guide; the `claude` call is a separate `terminal()` action the agent emits.
- Two ways a task gets triggered: **interactive** (a human runs `/claude-code` in chat, the
  agent delegates) and **programmatic** (Hermes code calls the CLI directly, no slash command).

### The two execution modes it documents

| Mode | Execution | Interactivity | Result delivery |
|---|---|---|---|
| **Print mode `claude -p`** (PREFERRED) | **blocking subprocess; runs the whole task autonomously, then exits** | none — internally agentic but invisible until exit ("blind until exit") | one **final JSON** object (`session_id`, `total_cost_usd`, `stop_reason`, `result`) |
| **tmux PTY** (only for multi-turn / human-decision work) | `claude` kept alive as a **background process** | the **agent** drives it via `send-keys` / `capture-pane` across turns | scraped from the pane |

Key consequence for both: the human's chat is always **you ↔ Hermes agent**; `claude` is never
wired directly into the chat. In tmux mode a human *can* see prompts and give feedback, but only
**relayed through the agent** (the agent polls `capture-pane`, spots a visual cue like the `❯`
prompt, surfaces it, and sends your answer back via `send-keys`) — the brittle "decision
detection" the matrix above already penalizes. (An operator with container shell access can
`tmux attach` to drive `claude` directly, but that is out-of-band, not the chat flow.)

### The session model it assumes

A Claude Code **session is persistent state on disk** (a `.jsonl` transcript under
`~/.claude/projects/<encoded-cwd>/`), **not a live process.** `/claude-code` itself starts no
session — it only loads the guide. "Continuable" means a *new* short-lived `--resume` subprocess
re-reads that file; no process is held open between calls.

### What it does NOT provide — the gap that justifies `golden_session`

The official doc is explicit that Claude Code sessions are **task-specific with no golden/primed
reuse**, and that multi-turn continuation *"requires explicit Hermes orchestration."* So
`/claude-code` gives us the substrate and the knowledge, but **not** the orchestration contract
our problem statement demands:

| Requirement (from Problem statement) | `/claude-code` alone | Supplied by `golden_session` |
|---|---|---|
| Stable context reused across many tasks, never polluted (#5) | **No golden pattern**; agent would have to invent and perfectly maintain GOLD discipline every task | prime-once GOLD + fork-per-task, enforced |
| Continuable on failure without losing progress (#6) | possible via `--resume`, but left to "explicit Hermes orchestration" | `continue_task` (resume/append), tracked |
| Bounded cost, no silent failure, single-writer safety | flags exist, but a guide only *suggests* — an LLM improvising `terminal()` calls can skip a cap, drop `cwd`, or double-write a session | F1–F10 enforced **in code**, deterministically |
| Decision points (#6) | brittle visual-cue relay (tmux) or none (print mode) | headless + structured sentinel (thread #3, Phase 2) |

**Conclusion.** `/claude-code` confirms and blesses our substrate choice (headless `claude -p`
+ JSON), but its orchestration is *agent-improvised and invariant-free by design*. The
problem statement requires *deterministic, code-enforced* invariants over a *reused* GOLD
session. `golden_session` is exactly that missing layer — it is not a replacement for
`/claude-code` but the engine the trigger should drive (see
[`05-integration-and-deployment.md`](./05-integration-and-deployment.md) Decisions A1/A2).

## Where ACP could still win

ACP has one genuinely attractive property for this use case: **decision detection is native**. Its typed permission-request events let a client see "the agent is asking permission for X" or "the agent needs a decision on Y" without parsing prompt conventions out of text. If tasks frequently hit permission boundaries or branching decisions, that could justify the GOLD gymnastics ACP requires.

The trade-off to cost precisely: ACP makes thread #3 (decision-detection protocol) trivial but makes GOLD implementation substantially harder. Headless makes GOLD trivial but requires inventing a decision-detection convention.

## Reference links

- Headless mode docs: https://docs.claude.com/en/docs/claude-code/headless
- Claude Agent SDK: https://docs.claude.com/en/api/agent-sdk/overview
- Hermes claude-code skill: https://github.com/NousResearch/hermes-agent/blob/main/skills/autonomous-ai-agents/claude-code/SKILL.md
- @agentclientprotocol/claude-agent-acp on npm: https://www.npmjs.com/package/@agentclientprotocol/claude-agent-acp
- claude-agent-acp on GitHub: https://github.com/agentclientprotocol/claude-agent-acp
