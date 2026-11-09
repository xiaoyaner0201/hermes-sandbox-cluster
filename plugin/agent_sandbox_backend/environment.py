"""agent-sandbox terminal backend for Hermes: a thin adapter over the self-hosted-agent-sandbox core.

Sandbox lifecycle, environment and file sync, code pull-back and host-command routing all live in
the core library (vendored under ``_vendor/``; source: github.com/xiaoyaner0201/self-hosted-agent-sandbox).
This module only maps Hermes's BaseEnvironment contract onto it:

* one Sandbox per Hermes task id, kept between turns, released at session close / idle reap;
* Hermes's session snapshot and cwd tracking run inside the sandbox (``_run_bash``), with the host
  environment sourced after the snapshot so current values win;
* the read-only copy of ``$HERMES_HOME/skills`` is seeded into the sandbox once at start.
"""
from __future__ import annotations

from pathlib import Path

from tools.environments.base import BaseEnvironment

from ._vendor.agent_sandbox.core import _TASK_PATH_RE, Sandbox, _popen_bash, bash_argv


def _session_task_cwd() -> str:
    """The task directory the client declared for this session (ACP ``session/new`` cwd), if any.

    Hermes freezes the client's cwd into the session (``agent.runtime_cwd``) but only applies it to the
    terminal for host-cwd Docker; other backends start in ``terminal.cwd``. Task runners (Multica) put
    every task under ``/workspaces/<ws>/<task>/workdir``, so adopt that path when it is one."""
    try:
        from agent.runtime_cwd import scoped_session_cwd
        cwd = scoped_session_cwd()
    except Exception:
        return ""
    return cwd if cwd and _TASK_PATH_RE.fullmatch(cwd.rstrip("/")) else ""


def _record_session_cwd(task_id: str, cwd: str) -> None:
    """Seed Hermes's per-session cwd record so terminal and file tools both start in ``cwd``."""
    try:
        from tools.terminal_tool import get_session_cwd, record_session_cwd
        from gateway.session_context import get_session_env
        for key in {task_id, get_session_env("HERMES_SESSION_KEY", "")} - {""}:
            if not get_session_cwd(key):
                record_session_cwd(key, cwd)
    except Exception:
        pass


def _skills_seed() -> tuple:
    try:
        from hermes_constants import get_hermes_home
        return ((Path(get_hermes_home()) / "skills", ".hermes/skills"),)
    except Exception:
        return ()


class AgentSandboxEnvironment(BaseEnvironment):
    """One SandboxClaim per Hermes task; persistent mode reuses the claim by task label."""

    _stdin_mode = "pipe"

    def __init__(self, *, cwd: str, timeout: int, task_id: str = "default",
                 container_config: dict | None = None, **_):
        # A task runner's declared task dir wins over the configured default (e.g. /workspaces).
        self._default_cwd = cwd
        self._task_cwd = _session_task_cwd() if not _TASK_PATH_RE.fullmatch((cwd or "").rstrip("/")) else ""
        if self._task_cwd:
            cwd = self._task_cwd
            _record_session_cwd(task_id, cwd)
        # Own key on purpose (agent_sandbox_persistent): Hermes fills the generic
        # container_persistent with True by default, which made every session claim survive teardown.
        self.sbx = Sandbox(cwd=cwd, timeout=timeout, session=task_id, config=container_config, app="hermes",
                           seed_dirs=_skills_seed(), extra_env_skip=("TERMINAL_ENV",))
        super().__init__(cwd=self.sbx.cwd, timeout=timeout)
        # Survive between turns of a session; removed at session close or by the idle reaper.
        self._session_scoped = True
        self.init_session()

    # ---- BaseEnvironment contract -----------------------------------------
    def _before_execute(self) -> None:
        self.sbx.before_command()

    def _wrap_command(self, command: str, cwd: str) -> str:
        # Source the host env AFTER the session snapshot so current values always win.
        return super()._wrap_command(self.sbx.env_prefix() + command, cwd)

    def execute(self, command: str, cwd: str = "", **kw) -> dict:
        if self._task_cwd and cwd == self._default_cwd:
            # Hermes plans the first command in terminal.cwd; run it in the task dir instead. Later
            # commands carry the cwd Hermes recorded (the task dir, or wherever the agent cd'd).
            cwd = self._task_cwd
            self._task_cwd = ""
        def _in_sandbox(c: str, d: str, t: int) -> dict:  # Hermes's own flow: snapshot, cwd markers, ...
            return BaseEnvironment.execute(self, c, d, **{**kw, "timeout": t})
        return self.sbx.execute(command, cwd or self.cwd, timeout=kw.get("timeout"), run_in_sandbox=_in_sandbox)

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120,
                  stdin_data: str | None = None):
        return _popen_bash(self.sbx.exec_argv(*bash_argv(cmd_string, login), stdin=stdin_data is not None), stdin_data)

    def cleanup(self, *, force_remove: bool = False):
        self.sbx.close(force_remove=force_remove)
