---
name: workbuddy-rewards
description: "Claim one local user's WorkBuddy daily rewards: the gas-station check-in and the Buddy travel cycle. Use when the user mentions WorkBuddy积分、签到、Buddy旅行、派猫猫 or a recurring daily reward check."
description_zh: "自动领取 WorkBuddy 每日积分：签到与 Buddy 旅行"
description_en: "Claim WorkBuddy daily credits: check-in and Buddy travel"
---

# WorkBuddy 积分助手

Two credit channels exist and this skill covers both: the daily **加油站签到** (100 credits) and the **Buddy 旅行** cycle (5–10 credits, one trip per day).

## Choose a command

Locate `scripts/main.py` relative to this file (normally `~/.workbuddy/skills/workbuddy-rewards/scripts/main.py`) and run it with Python 3.10+. Always pass the absolute script path so the working directory does not matter.

| 用户说 | 运行 |
| --- | --- |
| 检查、查看、状态 | `status`（只读） |
| 签到、领取签到积分 | `checkin` |
| Buddy旅行、派猫猫、旅行领奖 | `travel` |
| 明确要求完成今天全部积分任务 | `all` |
| 登录态或安装排障 | `doctor`（离线，不解密令牌） |

```bash
python "$HOME/.workbuddy/skills/workbuddy-rewards/scripts/main.py" status
python "$HOME/.workbuddy/skills/workbuddy-rewards/scripts/main.py" all
python "$HOME/.workbuddy/skills/workbuddy-rewards/scripts/main.py" travel --location coffee
```

If `python` is not on `PATH`, use the bundled wrapper and bypass the execution policy (unsigned local scripts are refused by default on many Windows machines):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.workbuddy\skills\workbuddy-rewards\scripts\run.ps1" status
```

Options: `--location <id|code>`, `--loop`, `--max-hours <h>`, `--poll-seconds <s>`, `--no-log`.

## Read the result

The script prints one JSON object and appends a sanitized one-line summary to `logs/result.log` unless `--no-log` is given. Summarize the outcome for the user.

- `already_checked`, `already_claimed`, `traveling`, `daily_limit_reached` are normal idempotent outcomes, not errors.
- Exit `0` success or normal no-op; `1` network, protocol, or business failure; `2` local login state missing or rejected.
- On exit `2`, tell the user to open WorkBuddy and sign in again. Never ask them to paste a token into chat.
- On `RUNTIME_NOT_FOUND`, the client could not be located: run `doctor`, and re-run with `WORKBUDDY_EXE` set if it is installed somewhere unusual.

A status-only request never authorizes `checkin`, `travel`, or `all` — those mutate the user's account, so ask first.

## What actually earns credits

- **签到** — once per day, 100 credits. Idempotent, never double-claims. Streak and weekly bonuses are granted server-side with no separate claim step.
- **旅行** — claim on arrival, then dispatch again. One trip per day, so the practical routine is: check in, dispatch once, claim that one arrival. Two to four runs a day is enough; running hourly earns nothing extra.
- All four locations pay the same and last the same (5–10 credits, 1–4 hours), so there is no location to optimize — only the gap between arrival and the next dispatch, which is what `--loop` reduces.
- `--loop` chains cycles in one foreground session (`... all --loop --max-hours 8 --poll-seconds 60`). It stops at the daily limit, when the window elapses, or on the first real failure. Use it only when the user asks for a maximization session.

**积分 (credits)** and **资源额度 (resource quota)** are different things: quota is the model allowance granted by the subscription plan and has nothing to claim. If the user mentions "平台奖励", ask which one they mean.

Stay on the verified endpoints. Never add lottery, makeup-card, task-claim, invite, referral, multi-account, or other growth-center actions, and never probe undocumented paths.

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
