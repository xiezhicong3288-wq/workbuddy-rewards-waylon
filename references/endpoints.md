# Endpoint contract

The implementation accepts only these HTTPS hosts:

- `copilot.tencent.com`
- `www.codebuddy.cn`
- `www.workbuddy.cn`

The preferred host is derived from `auth.endpoint` or `auth.domain` in the local WorkBuddy session. An unrecognized host is ignored rather than trusted.

## Check-in

- Status: `POST /v2/billing/meter/checkin-activity-status`
- Legacy status fallback, only after HTTP 404/405: `POST /v2/billing/meter/checkin-status`
- Claim: `POST /v2/billing/meter/daily-checkin`

`today_checked_in` has been inconsistent across client versions. A claim response with business code `10001` or a message containing `已签到`/`already checked` is therefore treated as the authoritative idempotent result.

Check-in runs in **seasons** with their own `start_time` / `end_time` and `season` number. Observed on 2026-09-30: season 9 ended 2026-09-29, season 10 started 2026-09-30. **When a season rolls over, `streak_days` restarts at 1 and `total_credits` restarts from that season's earnings** — this is not a failure. `total_credits` is a per-season figure, not a lifetime balance, so never report a drop across a season boundary as lost credits. A `success` response can legitimately omit `total_credits` on the first day of a season.

## Buddy travel

Preferred paths use `/v2/activity/growth`:

- `GET /v2/activity/growth/buddy/travel/status`
- `GET /v2/activity/growth/buddy/travel/config`
- `POST /v2/activity/growth/buddy/travel/depart` with `{ "location_id": ... }`
- `POST /v2/activity/growth/buddy/travel/claim` with `{ "record_id": ... }` when available

Older deployments use the same suffix below `/activity/growth`. The client falls back to that prefix only when the preferred request returns HTTP 404/405. It does not retry an ambiguous write response.

Travel state handling:

- `arrived`: claim once, then stop.
- `traveling`: report remaining/arrival time, no write.
- `idle` with `daily_limit_reached`: report the daily limit, no write.
- `idle`: read config, choose the requested location or the first location in the server's `sort` order, depart once, then stop.

Field behavior confirmed against the live API (v2 prefix, 2026-09):

- While `traveling`, `reward_credit` already carries the pending reward and `daily_limit_reached` flips to `true`; both are informational, not an error.
- `arrive_at` and `server_now` are Unix seconds from the server clock. Derive countdowns from them rather than the local clock.
- A second `travel` run while `traveling` must stay a read-only no-op; never re-depart.

## Budget coverage

Pay attention to the two different currencies; only the first one is "积分" and only it can be maximized here:

- **积分 (credits)** — earned by `daily-checkin` and the Buddy travel cycle. This is what the skill maximizes.
- **资源额度 (resource quota)** — model/compute allowance tied to the subscription plan. It is granted by the plan itself, not claimed. `/console/as/teams/me/quota` returns plan limits (`{"plan":"personal","limits":{...},"usage":{...}}`) with nothing to collect. The `get-user-resource-*-packages` paths return 404 on both `copilot.tencent.com` and `www.codebuddy.cn`.

Buddy 加油站 is the daily check-in itself, not a separate channel: `/v2/billing/meter/checkin-activity-status` reports `total_credits`, `streak_days`, `week_progress` and `streak_bonus_credit`, and streak/weekly bonuses are granted server-side with no extra claim endpoint. An `action_button` may advertise an unrelated expert-verification flow; ignore it.

Enumerated against the WorkBuddy desktop bundle (`resources/app.asar`) and confirmed live on 2026-09: the check-in and Buddy travel endpoints above are the **only** credit-earning paths. Everything else found nearby is not claimable:

- `/v2/activity/growth/buddy/info` — pet appearance plus `poll_interval_seconds`; no rewards.
- `/v2/activity/banner`, `/v2/activity/workbuddy/banner` — promotional banners (`activity is offline`).
- `/activity/workbuddy/invitation/*`, `/console/activity/ambassador/status` — referral flows, permanently out of scope.

Measured on 2026-09-29: `daily_limit_reached` turns `true` immediately after the first departure and remains `true` after claiming, with status back to `idle`. **One trip per day.** Plan for a single dispatch plus a single claim per day rather than continuous polling.

All travel locations return the same payout band (`reward_credit_min` 5 / `reward_credit_max` 10, `duration_hours_min` 1 / `duration_hours_max` 4), so location choice does not change expected yield. Total yield depends on how soon an arrival is claimed and a new trip dispatched, which is what `--loop` optimizes.

## Authentication

Requests use `Authorization: Bearer`, `X-User-Id`, and—when present—`X-Domain`, `X-Enterprise-Id`, and `X-Tenant-Id`. Never log these headers.

The session loader supports plaintext access tokens and WorkBuddy `sym-v1 / suite 1` encrypted envelopes. Encrypted values are decrypted by the installed WorkBuddy runtime's native storage binding; the script does not read or persist the WorkBuddy keyblob directly.
