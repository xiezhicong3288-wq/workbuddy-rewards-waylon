# Scheduled execution

Only create or change a schedule after the user explicitly asks.

Prefer an in-thread automation in WorkBuddy. Ask it to run the skill's absolute `scripts/main.py all` path with a working Python interpreter, parse the one-line JSON, remain quiet for normal no-op states, and notify only for points claimed, a new dispatch, failures, or required user action.

On Windows, call the interpreter directly rather than through the shell profile:

```bash
python "$HOME/.workbuddy/skills/workbuddy-rewards/scripts/main.py" all
```

The `scripts/run.ps1` wrapper is only a fallback for hosts without `python` on `PATH`; invoke it as `powershell -NoProfile -ExecutionPolicy Bypass -File ...` because unsigned local scripts are blocked by default on many Windows machines. `run.ps1` skips the Microsoft Store `python` aliases and probes remaining candidates for Python 3.10+. Set `WORKBUDDY_REWARDS_PYTHON` to pin one interpreter.

Recommended coverage:

- Run `all` once in the morning for check-in and initial travel dispatch.
- Run `travel` two or three more times later in the day so an arrived Buddy can be claimed.
- Commands are idempotent, but one travel invocation performs at most one transition.

`run.ps1` also looks for the Python bundled with the agent runtime (`~/.workbuddy/binaries/python/versions/*`). For a system Task Scheduler job independent of the agent, install Python 3.10+ or set `WORKBUDDY_REWARDS_PYTHON` to a trusted Python executable.

Note that a scheduled run needs the WorkBuddy client to have been installed and signed in at least once. Because the encrypted credential is decrypted with the client's own runtime, keep `WORKBUDDY_EXE` unset and let the cached path in the skill's `.runtime-cache` do the work.

Do not put tokens, session JSON, or copied credentials into the automation prompt, environment, command line, or task definition.
