---
name: workbuddy-rewards
description: "Manage one local user's WorkBuddy daily rewards: inspect authentication, query reward status, claim the Buddy gas-station check-in, and claim or dispatch Buddy travel. Use for WorkBuddy积分、签到、Buddy旅行、派猫猫、checkin、travel or recurring reward checks; do not use for multi-account farming or unrelated growth-center actions."
---

# WorkBuddy 积分助手

On the first invocation in a conversation, introduce the skill briefly:

> 你好，我是 WorkBuddy 积分助手。我可以检查 Buddy 加油站签到和 Buddy 旅行状态，也可以在你明确要求后完成签到、领奖或派遣。你可以说“检查我的 WorkBuddy 积分任务”或“帮我完成今天的积分任务”。

## Choose the safe command

Locate `scripts/main.py` relative to this file (normally `~/.workbuddy/skills/workbuddy-rewards/scripts/main.py`) and run it with Python 3.10+. Always use the absolute script path so the working directory does not matter.

- For “检查、查看、状态” requests, run `status`. It performs only status/config queries.
- For “签到、领取签到积分”, run `checkin`.
- For “Buddy旅行、派猫猫、旅行领奖”, run `travel`.
- For an explicit request to complete all daily reward work, run `all`.
- For authentication or installation troubleshooting, run `doctor`. It is offline and does not decrypt the token.
- Add `--no-log` to keep a run out of `logs/result.log`.

Prefer calling the interpreter directly. On Windows many machines have the default PowerShell execution policy set so that unsigned local scripts such as `scripts/run.ps1` are refused ("在此系统上禁止运行脚本"), which makes that route fail.

```bash
python "$HOME/.workbuddy/skills/workbuddy-rewards/scripts/main.py" status
python "$HOME/.workbuddy/skills/workbuddy-rewards/scripts/main.py" all
python "$HOME/.workbuddy/skills/workbuddy-rewards/scripts/main.py" travel --location coffee
```

Use the PowerShell wrapper only when `python` is not on `PATH`, and bypass the policy explicitly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.workbuddy\skills\workbuddy-rewards\scripts\run.ps1" status
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.workbuddy\skills\workbuddy-rewards\scripts\run.ps1" all
```

`run.ps1` skips the Microsoft Store `python` aliases in `%LOCALAPPDATA%\Microsoft\WindowsApps` and probes each candidate for Python 3.10+, so a broken `python` on `PATH` no longer blocks it. `WORKBUDDY_REWARDS_PYTHON` overrides the interpreter explicitly.

Read the single JSON object printed to stdout and summarize the outcome. Treat `already_checked`, `already_claimed`, `traveling`, and `daily_limit_reached` as normal idempotent outcomes. Do not infer permission for `checkin`, `travel`, or `all` from a status-only request: those commands mutate the user's WorkBuddy account. Never add lottery, makeup-card, task-claim, invite, multi-account, or other growth-center actions to the workflow.

If authentication fails, tell the user to open WorkBuddy and sign in again; never ask them to paste a token into chat. If a run reports `RUNTIME_NOT_FOUND`, the WorkBuddy client could not be located: run `doctor` first, and if the client is installed somewhere unusual, re-run with `WORKBUDDY_EXE` pointing at `WorkBuddy.exe`.

## Maximizing credits

Verified against the local client bundle and the live API on 2026-09: WorkBuddy exposes exactly **two** credit-earning channels, and this skill covers both.

1. **Buddy 加油站签到** — once per day, 100 credits. This *is* the gas station: its status payload contains `total_credits`, `streak_days`, `week_progress` and `streak_bonus_credit`. Streak and weekly bonuses are granted server-side and have no separate claim endpoint, so there is nothing extra to collect.
2. **Buddy 旅行** — dispatch the Buddy, then claim the reward on arrival.

Do not confuse **积分 (credits)** with **资源额度 (resource quota)**. Quota is the model/compute allowance tied to the subscription plan; it is granted by the plan and never claimed. `/console/as/teams/me/quota` only reports limits. If the user asks about "platform rewards", clarify which one they mean before touching any endpoint.

Everything else in the bundle is not a credit channel: `/v2/activity/growth/buddy/info` returns pet appearance data plus a polling hint and nothing claimable, `/v2/activity/banner` returns `activity is offline`, and the invitation endpoints are deliberately out of scope. Do not invent or probe undocumented endpoints.

All four travel locations pay identically (`reward_credit_min` 5, `reward_credit_max` 10, duration 1–4 hours), so there is no profitable location to pick. Total yield therefore depends only on **downtime**: claim as soon as the Buddy arrives, and re-dispatch immediately if the daily allotment allows.

Practical rules:

- Run `checkin` every day; it is idempotent and never double-claims.
- Run `travel` (or `all`) several times spread over the day so an arrival is claimed quickly.
- Add `--loop` to `travel`/`all` to chain cycles in one session: after claiming, it re-dispatches and then sleeps until `arrive_at` before checking again. Example: `... main.py all --loop --max-hours 8 --poll-seconds 60`. It stops by itself at the daily limit, when the window elapses, or on the first real failure. Use it only when the user asks for a foreground maximization session.
- Each loop iteration still re-reads state and performs at most one transition; an ambiguous write response ends the run rather than being retried.

Never claim resources that do not exist: no lottery, make-up card, task-invite, referral, multi-account, or other growth-center actions.

- Read only the current OS user's WorkBuddy session file. Do not copy, persist, print, summarize, or transmit access/refresh tokens.
- Network requests are restricted in code to known WorkBuddy/Tencent hosts and the documented check-in/travel paths.
- The encrypted credential helper may invoke the locally installed WorkBuddy executable in Node mode; the encrypted field enters over stdin and the decrypted access token returns only through an in-memory pipe.
- Runtime lookup prefers `WORKBUDDY_EXE`, then a locally cached install path, then the running WorkBuddy process. Registry entries and a quota-bounded disk search are last-resort fallbacks; any candidate must resolve to a file named `WorkBuddy.exe` with a sibling `resources` directory. The remembered path lives in `.runtime-cache`; it is never printed and never enters the result log.
- Logs contain only an allowlisted result summary. Never include HTTP headers, raw sessions, raw response bodies, account IDs, nicknames, phone numbers, or executable paths.
- Operate only the current local user's account. Refuse multi-account farming, credential import/export, or attempts to evade service limits.
- One travel invocation performs at most one transition. With `--loop`, every iteration re-reads state before performing at most one further transition, never polls until arrival blindly, and never retries an ambiguous write response.
- Requests stay on the documented endpoints. Do not discover, guess, or probe additional paths.

For endpoint contracts and compatibility fallbacks, read [references/endpoints.md](references/endpoints.md). For scheduled execution, read [references/scheduling.md](references/scheduling.md). The skill never creates a schedule unless the user explicitly asks.

## Result and exit conventions

The script emits one JSON object and appends a sanitized one-line summary to `logs/result.log` unless `--no-log` is supplied.

- Exit `0`: success or a normal no-op.
- Exit `1`: network, protocol, or business failure.
- Exit `2`: missing, invalid, or rejected local authentication.
