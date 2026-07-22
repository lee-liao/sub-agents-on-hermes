# Hermes email gateway on Windows with 163.com

This reference documents the Windows-specific Hermes email gateway setup used to enable email-triggered approvals and two-way email replies.

## Background

The Hermes gateway on Windows does not load the `~/.hermes/.env` file automatically. The orchestrator's email notifier can read the `EMAIL_*` variables that `hermes setup` writes, but the gateway service itself must also have them in its environment.

## 1. Load `.env` into the gateway service

The Windows service launchers are at:

- `%LOCALAPPDATA%\hermes\gateway-service\Hermes_Gateway.cmd`
- `%LOCALAPPDATA%\hermes\gateway-service\Hermes_Gateway.vbs`

Patch both to read the Hermes `.env` file and set each variable in the process environment before launching the gateway. Because Windows has no native dotenv support, this requires custom parsing in the launcher scripts.

## 2. 163.com authentication header mismatch

NetEase 163.com stamps `Authentication-Results` with properties like:

```text
spf=pass smtp.mail=you@hotmail.com; dkim=pass header.i=@hotmail.com
```

The Hermes email adapter expects `smtp.mailfrom` and `header.d`, so it treats the email as unauthenticated and drops it with `authentication failed`.

Workaround: set `EMAIL_TRUST_FROM_HEADER=true` as a Windows user environment variable:

```powershell
[Environment]::SetEnvironmentVariable("EMAIL_TRUST_FROM_HEADER", "true", "User")
```

Then restart the gateway from a fresh shell so the variable propagates. Because `EMAIL_ALLOWED_USERS` is already restricted, the risk of bypassing the header check is limited.

## 3. Default model cannot use tools

The Hermes default model was `tencent/hy3:free` via provider `nous`. When the gateway created an agent session for an inbound email, the model could not run the `pbi continue` command. It sent multiple email replies without ever resuming the workflow, causing an email reply loop.

Fix: set the default model to one that can use tools:

```powershell
hermes config set model.default kimi-for-coding
hermes config set model.provider kimi-coding
```

Restart the gateway.

## 4. Test the full loop

1. Send a new email to the Hermes address with a command like `pbi test approval 999-email`.
2. The gateway creates an agent session, which loads the workflow skill and runs the command.
3. The workflow pauses and sends an email notification.
4. Reply with `approve 999-email`.
5. The gateway agent runs `pbi continue 999-email --response "approve"`.
6. The workflow completes.

## 5. One account or two

The same `EMAIL_*` block can drive both sending pause notifications and receiving replies. The recipient can be set via `EMAIL_HOME_ADDRESS` or fall back to `EMAIL_ADDRESS`. If you want a separate "human" inbox, use `EMAIL_HOME_ADDRESS` or `PBI_EMAIL_TO` as an override.

## See also

- `windows-ai-agent-adaptation` SKILL.md
- `docs/human-in-the-loop.md` in the powerbi-workflow-orchestrator repo
