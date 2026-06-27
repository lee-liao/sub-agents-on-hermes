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

## Where ACP could still win

ACP has one genuinely attractive property for this use case: **decision detection is native**. Its typed permission-request events let a client see "the agent is asking permission for X" or "the agent needs a decision on Y" without parsing prompt conventions out of text. If tasks frequently hit permission boundaries or branching decisions, that could justify the GOLD gymnastics ACP requires.

The trade-off to cost precisely: ACP makes thread #3 (decision-detection protocol) trivial but makes GOLD implementation substantially harder. Headless makes GOLD trivial but requires inventing a decision-detection convention.

## Reference links

- Headless mode docs: https://docs.claude.com/en/docs/claude-code/headless
- Claude Agent SDK: https://docs.claude.com/en/api/agent-sdk/overview
- Hermes claude-code skill: https://github.com/NousResearch/hermes-agent/blob/main/skills/autonomous-ai-agents/claude-code/SKILL.md
- @agentclientprotocol/claude-agent-acp on npm: https://www.npmjs.com/package/@agentclientprotocol/claude-agent-acp
- claude-agent-acp on GitHub: https://github.com/agentclientprotocol/claude-agent-acp
