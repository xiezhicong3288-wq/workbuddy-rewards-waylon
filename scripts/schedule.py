"""Turn an uptime window into a concrete automation recipe.

The schedule is only useful when the machine is awake, and a 1-4 hour trip has to be
dispatched early enough to be claimed before the window closes. This module does that
arithmetic so a human does not have to.
"""

from __future__ import annotations

import argparse
from typing import Any

MIN_GAP_HOURS = 4          # longest possible trip
MAX_TRIP_HOURS = 4
CADENCE_CHOICES = (2, 3, 4, 6)


def parse_clock(value: str) -> int:
    """Parse HH:MM into minutes since midnight."""
    text = str(value).strip()
    for fmt_hint in (":", "："):
        if fmt_hint in text:
            hour_text, _, minute_text = text.partition(fmt_hint)
            break
    else:
        hour_text, minute_text = text, "0"
    hour, minute = int(hour_text), int(minute_text or 0)
    if not 0 <= hour <= 24 or not 0 <= minute <= 59:
        raise ValueError(f"时间格式无效: {value}")
    if hour == 24 and minute != 0:       # 24:00 means end of day
        raise ValueError(f"时间格式无效: {value}")
    return hour * 60 + minute


def _clock(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def recommend(start: str = "09:30", end: str = "18:30") -> dict[str, Any]:
    begin = parse_clock(start)
    stop = parse_clock(end)
    span = stop - begin
    if span <= 0:
        span += 24 * 60          # window crosses midnight
    hours = span / 60.0

    if hours < MIN_GAP_HOURS + 1:
        # Too short to dispatch and claim on separate ticks: one run must do both.
        loop = round(max(1.0, hours - 0.5), 1)
        return {
            "window": f"{_clock(begin)}-{'24:00' if stop == 24 * 60 else _clock(stop)}",
            "window_hours": round(hours, 1),
            "strategy": "single_run",
            "rrule": "FREQ=DAILY;BYHOUR=" + f"{begin // 60};BYMINUTE={begin % 60}",
            "command": f"... main.py all --loop --max-hours {loop} --poll-seconds 300",
            "slots": [_clock(begin)],
            "max_hours": loop,
            "notes": [
                f"开机窗口只有 {round(hours, 1)} 小时，不足以拆成「派遣＋领奖」两次。",
                "改为窗口开始时跑一次，让 --loop 自己等到到站并领奖。",
                f"行程最长 {MAX_TRIP_HOURS} 小时，若超出窗口当天这趟奖金会拿不到。",
            ],
        }

    # Prefer the widest cadence that still leaves three in-window slots: the third one is
    # the safety net for a dispatch that missed the first slot.
    cadence = 2
    for candidate in CADENCE_CHOICES:
        if span / 60.0 / candidate >= 3:
            cadence = candidate
    slots: list[str] = []
    offset = 0
    while offset <= span:
        slots.append(_clock(begin + offset))
        offset += cadence * 60
    max_hours = min(MAX_TRIP_HOURS, cadence)
    create_at = _clock(begin - cadence * 60)
    return {
        "window": f"{_clock(begin)}-{'24:00' if stop == 24 * 60 else _clock(stop)}",
        "window_hours": round(hours, 1),
        "strategy": "cadence",
        "cadence_hours": cadence,
        "rrule": f"FREQ=HOURLY;INTERVAL={cadence}",
        "command": f"... main.py all --loop --max-hours {max_hours} --poll-seconds 300",
        "slots": slots,
        "max_hours": max_hours,
        "create_at": create_at,
        "notes": [
            f"槽位落在开机窗口内的共 {len(slots)} 个：{', '.join(slots)}。",
            f"首尾相隔 {round((len(slots) - 1) * cadence, 1)} 小时，足够容纳一趟最长 {MAX_TRIP_HOURS} 小时的行程。",
            f"想让槽位正好从 {_clock(begin)} 开始，请在约 {create_at} 创建或更新这条自动化；"
            "那时没开机也没关系，槽位会是创建时刻起每隔 %d 小时。" % cadence,
            f"--loop --max-hours {max_hours} 让每次运行自己等到到站再领奖，不依赖下一个调度点。",
        ],
    }


def render(result: dict[str, Any]) -> str:
    lines = [
        f"开机窗口        {result['window']}（{result['window_hours']} 小时）",
        f"推荐策略        {result['strategy']}",
        f"rrule           {result['rrule']}",
        f"命令            {result['command']}",
    ]
    if result.get("create_at"):
        lines.append(f"理想创建时刻    {result['create_at']}")
    lines.append("槽位            " + " / ".join(result["slots"]))
    lines.append("")
    for note in result["notes"]:
        lines.append(f"  - {note}")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Suggest an automation recipe for an uptime window")
    parser.add_argument("--from", dest="start", default="09:30", help="开机时间 HH:MM（默认 09:30）")
    parser.add_argument("--to", dest="end", default="18:30", help="关机时间 HH:MM（默认 18:30）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = recommend(args.start, args.end)
    except (ValueError, TypeError) as error:
        print(f"参数错误: {error}")
        return 2
    print(render(result))
    return 0
