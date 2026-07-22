# Gateway launcher PATH fix for Windows

When a Python CLI package (e.g. `pbi-workflow`) is installed into the Hermes gateway's own virtualenv but the gateway agent still reports `pbi: command not found`, the launcher scripts are not exposing the venv `Scripts` directory to the gateway process. Restarting the gateway is not enough if the launcher never adds the directory to `PATH`.

## Files to patch

- `%LOCALAPPDATA%\hermes\gateway-service\Hermes_Gateway.cmd`
- `%LOCALAPPDATA%\hermes\gateway-service\Hermes_Gateway.vbs`

Add the venv `Scripts` directory to `PATH` before launching the gateway. Also add any extra `PYTHONPATH` entries required by the project (e.g. `sub-agents-on-hermes` for `golden_session`).

## `Hermes_Gateway.cmd` snippet

```cmd
@echo off
rem Hermes Agent Gateway - Messaging Platform Integration
cd /d C:\Users\liao_\AppData\Local\hermes

rem Load environment variables from .env file
set "ENV_FILE=C:\Users\liao_\AppData\Local\hermes\.env"
if exist "%ENV_FILE%" (
    for /f "usebackq delims=" %%i in (`"D:\Program Files\Python311\python.exe" -c "import os; p=os.path.expandvars(r'%ENV_FILE%'); [print(f'set \"%s=%s\"' % (k.strip(), v.strip())) for k,v in (l.split('=',1) for l in open(p).read().splitlines() if l.strip() and not l.strip().startswith('#'))]"`) do %%i
)

set "HERMES_HOME=C:\Users\liao_\AppData\Local\hermes"
set "PYTHONIOENCODING=utf-8"
set "HERMES_GATEWAY_DETACHED=1"
set "VIRTUAL_ENV=C:\Users\liao_\AppData\Local\hermes\hermes-agent\venv"
set "PATH=C:\Users\liao_\AppData\Local\hermes\hermes-agent\venv\Scripts;%PATH%"
set "PYTHONPATH=C:\Users\liao_\AppData\Local\hermes\hermes-agent;C:\Users\liao_\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages;D:\MyCode\Ivan\sub-agents-on-hermes;%PYTHONPATH%"
"D:\Program Files\Python311\pythonw.exe" -m hermes_cli.main gateway run
```

## `Hermes_Gateway.vbs` snippet

```vbs
' Hermes Agent Gateway - Messaging Platform Integration
Option Explicit
Dim sh, env, existing_path, existing_pp, fso, envFile, ts, line, eqPos, key, value
Set sh = CreateObject("WScript.Shell")
Set env = sh.Environment("PROCESS")
Set fso = CreateObject("Scripting.FileSystemObject")

envFile = "C:\Users\liao_\AppData\Local\hermes\.env"
If fso.FileExists(envFile) Then
  Set ts = fso.OpenTextFile(envFile, 1, False)
  Do While Not ts.AtEndOfStream
    line = ts.ReadLine()
    line = Trim(line)
    If Len(line) > 0 And Left(line, 1) <> "#" Then
      eqPos = InStr(line, "=")
      If eqPos > 0 Then
        key = Trim(Left(line, eqPos - 1))
        value = Trim(Mid(line, eqPos + 1))
        env.Item(key) = value
      End If
    End If
  Loop
  ts.Close
End If

env.Item("HERMES_HOME") = "C:\Users\liao_\AppData\Local\hermes"
env.Item("PYTHONIOENCODING") = "utf-8"
env.Item("HERMES_GATEWAY_DETACHED") = "1"
env.Item("VIRTUAL_ENV") = "C:\Users\liao_\AppData\Local\hermes\hermes-agent\venv"
existing_path = env.Item("PATH")
env.Item("PATH") = "C:\Users\liao_\AppData\Local\hermes\hermes-agent\venv\Scripts;" & existing_path
existing_pp = env.Item("PYTHONPATH")
If Len(existing_pp) > 0 Then
  env.Item("PYTHONPATH") = "C:\Users\liao_\AppData\Local\hermes\hermes-agent;C:\Users\liao_\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages;D:\MyCode\Ivan\sub-agents-on-hermes;" & existing_pp
Else
  env.Item("PYTHONPATH") = "C:\Users\liao_\AppData\Local\hermes\hermes-agent;C:\Users\liao_\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages;D:\MyCode\Ivan\sub-agents-on-hermes"
End If
sh.CurrentDirectory = "C:\Users\liao_\AppData\Local\hermes"
sh.Run """D:\Program Files\Python311\pythonw.exe"" -m hermes_cli.main gateway run", 0, False
```

## Why the user PATH change is not enough

- The user PATH is updated, but Windows services or direct-spawn gateway processes may not refresh it until a full restart from the launcher.
- The `hermes-agent` venv is the Python environment the gateway actually runs in. Adding the venv `Scripts` to the launcher guarantees `pbi.exe` resolves regardless of the parent process PATH.

## After patching

1. Restart the gateway: `hermes gateway restart`
2. Verify the agent can find `pbi` by sending a test command from email or Discord: `pbi status <case-id>`
3. If the agent still reports `command not found`, inspect the running process PATH with `Get-Process` or Process Explorer to confirm the venv `Scripts` is included.
