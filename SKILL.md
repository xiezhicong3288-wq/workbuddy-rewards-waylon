---
name: workbuddy-rewards
description: "Claim the local user's WorkBuddy credits: the daily gas-station check-in, the Buddy travel cycle, and the growth-center task board. Use when the user mentions WorkBuddy积分、签到、Buddy旅行、派猫猫、任务中心、成长计划、积分任务、去完成/领取 or a recurring daily reward check."
description_zh: "自动签到、旅行与任务中心领积分"
description_en: "Claim WorkBuddy credits: check-in, travel, growth tasks"
---

# WorkBuddy 积分助手

Three credit channels exist and this skill covers all of them: the daily **加油站签到** (100 credits),
the **Buddy 旅行** cycle (5–10 credits, one trip per day), and the **任务中心** task board
(discrete "体验某某" tasks worth 50–300 credits each).

## Choose a command

Locate `scripts/main.py` relative to this file (normally `~/.workbuddy/skills/workbuddy-rewards/scripts/main.py`) and run it with Python 3.10+. Always pass the absolute script path so the working directory does not matter.

| 用户说 | 运行 |
| --- | --- |
| 检查、查看、状态 | `status`（只读） |
| 签到、领取签到积分 | `checkin` |
| Buddy旅行、派猫猫、旅行领奖 | `travel` |
| 任务中心、成长任务、有哪些任务待完成 | `tasks`（只读） |
| 把能领的任务积分领掉 | `tasks --claim` |
| 明确要求完成今天全部积分任务 | `all` |
| 登录态或安装排障 | `doctor`（离线，不解密令牌） |
| 要建/改定时、问多久跑一次 | `schedule --from <开机> --to <关机>`（离线，只算数） |

```bash
python "$HOME/.workbuddy/skills/workbuddy-rewards/scripts/main.py" status
python "$HOME/.workbuddy/skills/workbuddy-rewards/scripts/main.py" all
python "$HOME/.workbuddy/skills/workbuddy-rewards/scripts/main.py" tasks --claim
```

If `python` is not on `PATH`, use the bundled wrapper and bypass the execution policy (unsigned local scripts are refused by default on many Windows machines):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.workbuddy\skills\workbuddy-rewards\scripts\run.ps1" status
```

Options: `--location <id|code>`, `--loop`, `--max-hours <h>`, `--poll-seconds <s>`, `--no-log`.

When the user asks how often to run this, or wants a schedule created or changed, run
`schedule` with their uptime window first (`... main.py schedule --from 09:30 --to 18:30`)
and follow its rrule and `--max-hours` instead of guessing a cadence. Copy the ready-made
prompt from [references/automation-prompt.md](references/automation-prompt.md) and fill in
the interpreter path, the script path, and the recommended `--max-hours`.

## Read the result

The script prints one JSON object and appends a sanitized one-line summary to `logs/result.log` unless `--no-log` is given. Summarize the outcome for the user.

- `already_checked`, `already_claimed`, `traveling`, `daily_limit_reached` are normal idempotent outcomes, not errors.
- Exit `0` success or normal no-op; `1` network, protocol, or business failure; `2` local login state missing or rejected.
- On exit `2`, tell the user to open WorkBuddy and sign in again. Never ask them to paste a token into chat.
- On `RUNTIME_NOT_FOUND`, the client could not be located: run `doctor`, and re-run with `WORKBUDDY_EXE` set if it is installed somewhere unusual.

A status-only request never authorizes `checkin`, `travel`, `all`, or `tasks --claim` — those mutate the user's account, so ask first. Plain `tasks` is always safe: it lists the board without writing.

## What actually earns credits

- **签到** — once per day, 100 credits. Idempotent, never double-claims. Streak and weekly bonuses are granted server-side with no separate claim step.
- Check-in runs in dated **seasons**. When one rolls over, `streak_days` restarts at 1 and `total_credits` resets to that season's earnings — **not lost credits**. Never report the reset as a problem, and expect `total_credits` to be missing on a season's first day.
- **旅行** — claim on arrival, then dispatch again. One trip per day, so the practical routine is: check in, dispatch once, claim that one arrival. Two to four runs a day is enough; running hourly earns nothing extra.
- All four locations pay the same and last the same (5–10 credits, 1–4 hours), so there is no location to optimize — only the gap between arrival and the next dispatch, which is what `--loop` reduces.
- `--loop` chains cycles in one foreground session (`... all --loop --max-hours 8 --poll-seconds 60`). It stops at the daily limit, when the window elapses, or on the first real failure. Use it only when the user asks for a maximization session.
- **任务中心** — each task pays 50–300 credits and has five states: `not_accepted → accepted → in_progress → completed → claimed`. Two rules govern everything:
  - **Usage only counts after acceptance.** The counter starts at `0/n` on acceptance and never credits work done earlier, so run `tasks --claim` once before starting any 体验/聊天 style task, then again later to collect.
  - **Only `completed` tasks pay.** Claiming anything else returns HTTP 400 `task not completed`, which the script reports as pending, not as a failure.
- `tasks --claim` therefore does three things per run: accept every unopened task (one batched request), claim every completed one, and list what is still waiting with a `hint` describing the action. Tasks needing an action inside WorkBuddy (创建画布、召唤专家、真实对话) or outside it (关注公众号) stay listed — say which ones remain and what each needs; never claim they are done.
- `Library_read` and similar "read/open/spend time in" tasks count a **client view event**, not an API call: driving the library through `space_api.py` leaves the counter at `0/1`, and opening the node URL yourself does complete it. Hand the user the node link instead of burning calls on it.

**积分 (credits)** and **资源额度 (resource quota)** are different things: quota is the model allowance granted by the subscription plan and has nothing to claim. If the user mentions "平台奖励", ask which one they mean.

Stay on the verified endpoints listed in [references/endpoints.md](references/endpoints.md). Never add invite, referral, multi-account, lottery redemption, or Buddy-prize shipping actions, and never probe undocumented paths.

## Where it runs

This skill drives a **desktop** WorkBuddy install: it reads that machine's login session and decrypts it with the desktop client binary.

- 桌面端 — 完整可用。
- 手机 App「连接电脑」模式 — 可用：手机只下发指令，真正执行的是桌面端，两端需登录同一微信账号。
- 手机 App「云端工作」模式 / 任何云端沙箱 — **不可用**，会以 `NO_AUTH_FILE` 或 `RUNTIME_NOT_FOUND` 退出。这是预期行为：不要为适配云端而改写逻辑、伪造凭据或改用其它凭据来源。

## Safety invariants

- Read only the current OS user's own session file. Tokens never leave the process, never touch disk, never enter logs.
- Network requests are restricted in code to known WorkBuddy/Tencent hosts and the verified paths.
- Logs carry an allowlisted result summary only — no headers, response bodies, account IDs, or executable paths.
- One invocation performs at most one state transition; an ambiguous write response ends the run rather than being retried.
- Refuse multi-account farming, credential import/export, and attempts to evade service limits.

Endpoint contracts and compatibility fallbacks: [references/endpoints.md](references/endpoints.md). Scheduled execution: [references/scheduling.md](references/scheduling.md). Never create a schedule unless the user explicitly asks.
