"""Task Scheduler side (W0-07). Why Task Scheduler and not NSSM: NSSM is not installed here (it would be an
unmaintained third-party binary from the internet), while S4U tasks are built in, need no stored password and
already run this server's watchdogs. An S4U task runs in session 0, so it does not depend on any RDP session.

Each service = task genesis_<name>: action `<W0 venv python> -E -m genesis_service run <name> --config <file>`,
principal = the installing user, S4U, least privilege; triggers: at boot + every minute forever (autostart),
MultipleInstances IgnoreNew (so the minute trigger only revives a dead runner), no time limit, restart on failure.
The runner itself restarts the program within seconds; the minute trigger covers a dead runner.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .config import Paths, Service

XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{description}</Description>
    <URI>\\{task}</URI>
  </RegistrationInfo>
  <Triggers>{triggers}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{sid}</UserId>
      <LogonType>S4U</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{root}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""

AUTOSTART_TRIGGERS = """
    <BootTrigger>
      <Enabled>true</Enabled>
    </BootTrigger>
    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT1M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>"""


class TaskError(RuntimeError):
    pass


def ps(command: str) -> str:
    """Run a PowerShell command (inline, no script file) and return stdout; raise on failure."""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "$ErrorActionPreference = 'Stop'; [Console]::OutputEncoding = [Text.Encoding]::UTF8; " + command],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise TaskError((r.stderr or r.stdout).strip()[-800:])
    return r.stdout.strip()


def q(text: str) -> str:
    """PowerShell single-quoted literal."""
    return "'" + text.replace("'", "''") + "'"


def current_sid() -> str:
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                         encoding="mbcs", errors="replace", check=True).stdout
    return out.strip().split(",")[-1].strip('"')


def runner_python(p: Paths) -> Path:
    return p.root / "apps" / "w0-core" / ".venv" / "Scripts" / "python.exe"


def task_xml(svc: Service, p: Paths, config_file: Path, sid: str) -> str:
    triggers = AUTOSTART_TRIGGERS.format(start=datetime.now().replace(second=0, microsecond=0).isoformat()) \
        if svc.autostart else ""
    arguments = f'-E -m genesis_service run {svc.name} --config "{config_file}"'
    return XML.format(description=escape(f"Genesis service {svc.name}: {svc.description} (W0-07, genesis_service)"),
                      task=svc.task_name, triggers=triggers, sid=escape(sid), python=escape(str(runner_python(p))),
                      arguments=escape(arguments), root=escape(str(p.root)))


def register(svc: Service, p: Paths, config_file: Path) -> None:
    xml = task_xml(svc, p, config_file, current_sid())
    tmp = p.run / f"{svc.name}.task.xml"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(xml, encoding="utf-16")
    try:
        ps(f"Register-ScheduledTask -TaskName {q(svc.task_name)} -Xml ([IO.File]::ReadAllText({q(str(tmp))})) -Force | Out-Null")
    finally:
        tmp.unlink(missing_ok=True)


def unregister(svc: Service) -> None:
    ps(f"Unregister-ScheduledTask -TaskName {q(svc.task_name)} -Confirm:$false")


def start(svc: Service) -> None:
    ps(f"Start-ScheduledTask -TaskName {q(svc.task_name)}")


def end(svc: Service) -> None:
    ps(f"Stop-ScheduledTask -TaskName {q(svc.task_name)}")


def info(svc: Service) -> dict | None:
    out = ps(f"""$t = Get-ScheduledTask -TaskName {q(svc.task_name)} -ErrorAction SilentlyContinue
if ($t) {{ $i = $t | Get-ScheduledTaskInfo
  [pscustomobject]@{{state = "$($t.State)"; logon = "$($t.Principal.LogonType)"; run_level = "$($t.Principal.RunLevel)";
    triggers = @($t.Triggers | ForEach-Object {{ $_.CimClass.CimClassName }}); last_run = "$($i.LastRunTime)";
    last_result = $i.LastTaskResult; next_run = "$($i.NextRunTime)"}} | ConvertTo-Json -Compress }}
exit 0""")
    return json.loads(out) if out else None
