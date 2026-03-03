#!/usr/bin/env python3

import json
import sys
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo

# Set your local timezone here
LOCAL_TZ = ZoneInfo("America/New_York")


def parse_iso8601_z(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def day_bounds_utc(target_date: date, tz: ZoneInfo):
    start_local = datetime.combine(target_date, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and a_end > b_start


def resolve_target_date(arg: str | None, tz: ZoneInfo) -> date:
    today = datetime.now(tz).date()

    if arg is None or arg.lower() == "today":
        return today
    if arg.lower() == "tomorrow":
        return today + timedelta(days=1)

    try:
        return date.fromisoformat(arg)
    except ValueError:
        raise SystemExit('Date must be "today", "tomorrow", or YYYY-MM-DD.')


def format_time_range(start_local: datetime, end_local: datetime, all_day: bool) -> str:
    if all_day:
        return "All Day"

    start_str = start_local.strftime("%-I:%M %p")
    end_str = end_local.strftime("%-I:%M %p")
    return f"{start_str} – {end_str}"


def main():
    if len(sys.argv) < 2:
        print("Usage: events_for_day.py <events.json> [today|tomorrow|YYYY-MM-DD]")
        sys.exit(2)

    json_path = sys.argv[1]
    day_arg = sys.argv[2] if len(sys.argv) >= 3 else None

    target_date = resolve_target_date(day_arg, LOCAL_TZ)
    day_start_utc, day_end_utc = day_bounds_utc(target_date, LOCAL_TZ)

    with open(json_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    events = payload.get("events", [])
    matches = []

    for e in events:
        if "start" not in e or "end" not in e:
            continue

        start_utc = parse_iso8601_z(e["start"])
        end_utc = parse_iso8601_z(e["end"])

        if overlaps(start_utc, end_utc, day_start_utc, day_end_utc):
            start_local = start_utc.astimezone(LOCAL_TZ)
            end_local = end_utc.astimezone(LOCAL_TZ)

            matches.append({
                "title": e.get("title") or "(No Title)",
                "start": start_local,
                "end": end_local,
                "all_day": e.get("allDay", False),
            })

    matches.sort(key=lambda x: x["start"])

    # Header
    print(target_date.strftime("%A, %B %-d, %Y"))
    print()

    if not matches:
        print("No events.")
        return

    for m in matches:
        time_str = format_time_range(m["start"], m["end"], m["all_day"])
        print(f"{time_str:<18} {m['title']}")


if __name__ == "__main__":
    main()