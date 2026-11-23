#!/usr/bin/env python3
"""Read pending Planify tasks and print one JSON object for the Plasma widget.

The database is opened read-only. Sources.data can hold account credentials,
so it is never selected. This script always exits 0; failures are reported in
the JSON "ok" and "error" fields.
"""

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.parse

DEFAULT_DB = (
    "~/.var/app/io.github.alainm23.planify/data/"
    "io.github.alainm23.planify/database.db"
)
PLANIFY_APP_ID = "io.github.alainm23.planify"
GROUPS = ("overdue", "today", "soon", "important")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

COLOR_NAMES = {
    "berry_red": "#b8256f", "red": "#db4035", "orange": "#ff9933",
    "yellow": "#fad000", "olive_green": "#afb83b", "lime_green": "#7ecc49",
    "green": "#299438", "mint_green": "#6accbc", "teal": "#158fad",
    "sky_blue": "#14aaf5", "light_blue": "#96c3eb", "blue": "#4073ff",
    "grape": "#884dff", "violet": "#af38eb", "lavender": "#eb96eb",
    "magenta": "#e05194", "salmon": "#ff8d85", "charcoal": "#808080",
    "grey": "#b8b8b8", "taupe": "#ccac93",
}
_HEX_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")

_ISO_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})"
    r"(?:[Tt ]([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:[.,]([0-9]{1,9}))?)?"
    r"\s*([Zz]|[+-][0-9]{2}(?::?[0-9]{2})?)?)?"
)

TASKS_SQL = """
SELECT i.id, i.content, i.due, i.priority, p.name, p.color
FROM Items AS i
JOIN Projects AS p ON p.id = i.project_id
WHERE COALESCE(i.checked, 0) = 0
  AND COALESCE(i.is_deleted, 0) = 0
  AND COALESCE(i.is_trash, 0) = 0
  AND LOWER(COALESCE(i.item_type, '')) <> 'note'
  AND COALESCE(p.is_deleted, 0) = 0
  AND COALESCE(p.is_archived, 0) = 0
  AND NOT EXISTS (
        SELECT 1 FROM Sections AS sec
        WHERE sec.id = i.section_id AND i.section_id <> ''
          AND (sec.is_deleted = 1 OR sec.is_archived = 1))
  AND NOT EXISTS (
        SELECT 1 FROM Sources AS src
        WHERE src.id = p.source_id AND p.source_id <> ''
          AND src.is_visible = 0)
"""

LINKS_SQL = """
SELECT id, parent_id,
       COALESCE(checked, 0) <> 0
       OR COALESCE(is_deleted, 0) <> 0
       OR COALESCE(is_trash, 0) <> 0
FROM Items
"""

# Existence check only: no value column of Sources is selected.
CALDAV_SQL = """
SELECT 1 FROM Sources
WHERE source_type = 'caldav' AND (is_visible IS NULL OR is_visible <> 0)
LIMIT 1
"""


class ArgumentError(Exception):
    """Raised instead of letting argparse print usage and exit non-zero."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ArgumentError(message)


def parse_args(argv):
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--soon-days", type=int, default=3)
    parser.add_argument("--min-priority", type=int, default=4)
    parser.add_argument("--stale-minutes", type=int, default=60)
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    if args.soon_days < 0 or args.stale_minutes < 0:
        raise ArgumentError("negative value")
    if not 1 <= args.min_priority <= 4:
        raise ArgumentError("priority out of range")
    return args


def parse_moment(value):
    """Return (local_date, local_datetime_or_None), or None if unparseable.

    A None datetime means the value was date-only (all-day).
    """
    if not isinstance(value, str):
        return None
    match = _ISO_RE.fullmatch(value.strip())
    if not match:
        return None
    year, month, day, hour, minute, second, fraction, offset = match.groups()
    try:
        if hour is None:
            return dt.date(int(year), int(month), int(day)), None
        micro = int((fraction or "").ljust(6, "0")[:6] or 0)
        moment = dt.datetime(int(year), int(month), int(day), int(hour),
                             int(minute), int(second or 0), micro,
                             tzinfo=_parse_offset(offset))
        # Naive values are interpreted as local by astimezone().
        local = moment.astimezone()
    except (ValueError, OverflowError, OSError):
        return None
    return local.date(), local


def _parse_offset(offset):
    if offset is None:
        return None
    if offset in ("Z", "z"):
        return dt.timezone.utc
    digits = offset[1:].replace(":", "")
    hours, minutes = int(digits[:2]), int(digits[2:] or 0)
    if minutes > 59:
        raise ValueError("bad offset minutes")
    delta = dt.timedelta(hours=hours, minutes=minutes)
    return dt.timezone(-delta if offset[0] == "-" else delta)


def parse_due(raw):
    """Return (local_date, local_datetime_or_None, is_recurring) or None."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    parsed = parse_moment(data.get("date"))
    if parsed is None:
        return None
    return parsed[0], parsed[1], _truthy(data.get("is_recurring"))


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return value is True or (isinstance(value, int) and value != 0)


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _priority(value):
    try:
        return max(1, min(4, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 1


def normalize_color(value):
    text = _text(value)
    if _HEX_COLOR_RE.fullmatch(text):
        return text
    return COLOR_NAMES.get(text.lower(), "")


def format_time(moment):
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{moment.hour % 12 or 12}:{moment.minute:02d} {suffix}"


def due_label(date, moment, today):
    if date is None:
        return ""
    days = (date - today).days
    if days == 0:
        return f"Today {format_time(moment)}" if moment else "Today"
    if days == 1:
        return f"Tomorrow {format_time(moment)}" if moment else "Tomorrow"
    if days == -1:
        return "Yesterday"
    if -7 < days < 0:
        return f"{-days} days ago"
    if 0 < days < 7:
        return WEEKDAYS[date.weekday()]
    label = f"{MONTHS[date.month - 1]} {date.day}"
    return label if date.year == today.year else f"{label}, {date.year}"


def classify(date, priority, today, soon_days, min_priority):
    if date is not None:
        days = (date - today).days
        if days < 0:
            return "overdue"
        if days == 0:
            return "today"
        if days <= soon_days:
            return "soon"
    return "important" if priority >= min_priority else None


def build_tasks(rows, today, soon_days, min_priority):
    keyed = []
    for item_id, content, raw_due, raw_priority, name, color in rows:
        priority = _priority(raw_priority)
        due = parse_due(raw_due)
        date, moment, recurring = due if due else (None, None, False)
        group = classify(date, priority, today, soon_days, min_priority)
        if group is None:
            continue
        title = _text(content)
        if moment is not None:
            due_value = moment.isoformat(timespec="seconds")
        else:
            due_value = date.isoformat() if date else None
        task = {
            "id": _text(item_id),
            "title": title,
            "project": _text(name),
            "project_color": normalize_color(color),
            "priority": priority,
            "due": due_value,
            "all_day": date is not None and moment is None,
            "due_label": due_label(date, moment, today),
            "group": group,
            "is_recurring": recurring,
        }
        key = (
            GROUPS.index(group),
            date is None,
            date.toordinal() if date else 0,
            moment is not None,
            moment.timestamp() if moment else 0.0,
            -priority,
            title.casefold(),
            title,
            task["id"],
        )
        keyed.append((key, task))
    keyed.sort(key=lambda pair: pair[0])
    return [task for _, task in keyed]


def detect_planify_running():
    """Return True/False from `flatpak ps`, or None if it cannot be determined."""
    try:
        result = subprocess.run(
            ["flatpak", "ps", "--columns=application"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=3,
            encoding="utf-8", errors="replace", check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return any(line.strip() == PLANIFY_APP_ID
               for line in result.stdout.splitlines())


def database_mtime(path):
    """Return the file's mtime as a local datetime, or None."""
    try:
        stamp = os.stat(os.path.expanduser(path)).st_mtime
        return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).astimezone()
    except (OSError, OverflowError, ValueError):
        return None


def build_sync(modified, now, stale_minutes, running, has_caldav):
    if modified is None:
        return {**empty_sync(), "planify_running": running}
    age = max(0, int((now - modified).total_seconds() // 60))
    return {
        "last_sync": modified.isoformat(timespec="seconds"),
        "age_minutes": age,
        "stale": running is False and age > stale_minutes and has_caldav,
        "planify_running": running,
    }


def empty_sync():
    return {"last_sync": None, "age_minutes": None, "stale": False,
            "planify_running": None}


def open_readonly(path):
    absolute = os.path.abspath(os.path.expanduser(path))
    quoted = urllib.parse.quote(os.fsencode(absolute), safe="/")
    conn = sqlite3.connect(f"file://{quoted}?mode=ro", uri=True, timeout=2.0,
                           isolation_level=None)
    conn.text_factory = lambda raw: raw.decode("utf-8", "replace")
    conn.execute("PRAGMA query_only=ON")
    return conn


def has_closed_ancestor(item_id, links):
    """Walk up parent links; a missing parent or a cycle ends the walk."""
    seen = {item_id}
    parent = links[item_id][0] if item_id in links else None
    while parent not in (None, "") and parent not in seen:
        seen.add(parent)
        link = links.get(parent)
        if link is None:
            return False
        if link[1]:
            return True
        parent = link[0]
    return False


def read_database(path):
    """Return (task_rows, has_visible_caldav, mtime) from one read snapshot."""
    conn = open_readonly(path)
    try:
        conn.execute("BEGIN")
        rows = conn.execute(TASKS_SQL).fetchall()
        links = {row[0]: (row[1], bool(row[2]))
                 for row in conn.execute(LINKS_SQL).fetchall()}
        rows = [row for row in rows if not has_closed_ancestor(row[0], links)]
        has_caldav = conn.execute(CALDAV_SQL).fetchone() is not None
        # Taken under the shared lock, so no write lands mid-read.
        modified = database_mtime(path)
        conn.execute("ROLLBACK")
        return rows, has_caldav, modified
    finally:
        conn.close()


def _db_error(exc):
    """Map to fixed strings so no path or SQL can leak into the output."""
    message = str(exc).lower()
    if "locked" in message or "busy" in message:
        return "database locked"
    if "no such" in message:
        return "database schema not recognized"
    if "not a database" in message or "malformed" in message:
        return "database unreadable"
    return "database unavailable"


def failure(error, now):
    return {
        "ok": False,
        "error": error,
        "generated_at": now.isoformat(timespec="seconds"),
        "counts": {group: 0 for group in GROUPS},
        "tasks": [],
        "sync": empty_sync(),
    }


def build_output(argv, detect=detect_planify_running):
    now = dt.datetime.now().astimezone()
    try:
        args = parse_args(argv)
    except ArgumentError:
        return failure("invalid arguments", now)

    if args.now is not None:
        parsed = parse_moment(args.now)
        if parsed is None:
            return failure("invalid arguments", now)
        date, moment = parsed
        try:
            now = moment or dt.datetime(date.year, date.month,
                                        date.day).astimezone()
        except (ValueError, OverflowError, OSError):
            return failure("invalid arguments", dt.datetime.now().astimezone())

    if not os.path.isfile(os.path.expanduser(args.db)):
        return failure("database not found", now)
    try:
        rows, has_caldav, modified = read_database(args.db)
    except sqlite3.Error as exc:
        return failure(_db_error(exc), now)

    tasks = build_tasks(rows, now.date(), args.soon_days, args.min_priority)
    counts = {group: 0 for group in GROUPS}
    for task in tasks:
        counts[task["group"]] += 1
    return {
        "ok": True,
        "error": None,
        "generated_at": now.isoformat(timespec="seconds"),
        "counts": counts,
        "tasks": tasks,
        "sync": build_sync(modified, now, args.stale_minutes, detect(),
                           has_caldav),
    }


def main(argv=None):
    try:
        output = build_output(sys.argv[1:] if argv is None else argv)
    except Exception:  # The widget must always receive valid JSON.
        output = failure("internal error", dt.datetime.now().astimezone())
    try:
        sys.stdout.write(json.dumps(output) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        pass
    return 0


if __name__ == "__main__":
    code = main()
    try:
        sys.stdout.close()
    except (BrokenPipeError, ValueError):
        pass
    os._exit(code)
