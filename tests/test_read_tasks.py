"""Tests for read_tasks.py. All data is invented and built in tmp_path."""

import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "package/contents/code/read_tasks.py"
NOW = "2026-11-25T12:00:00-05:00"  # A Wednesday, in the default test TZ.
SECRET = "SENTINEL-SECRET-7f3a91"

ITEM_COLUMNS = (
    "id content description due added_at completed_at updated_at section_id "
    "project_id parent_id priority child_order checked is_deleted day_order "
    "collapsed pinned labels extra_data item_type calendar_event_uid "
    "deadline_date responsible_uid is_trash"
).split()
PROJECT_COLUMNS = (
    "id name color backend_type inbox_project team_inbox child_order "
    "is_deleted is_archived is_favorite shared view_style sort_order "
    "parent_id collapsed icon_style emoji show_completed description "
    "due_date inbox_section_hidded sync_id source_id calendar_url sorted_by "
    "calendar_source_uid markdown_setting extra_data"
).split()
SOURCE_COLUMNS = (
    "id source_type display_name added_at updated_at is_visible child_order "
    "sync_server last_sync data"
).split()
EMPTY_SYNC = {"last_sync": None, "age_minutes": None, "stale": False,
              "planify_running": None}
TASK_KEYS = {
    "id", "title", "project", "project_color", "priority", "due", "all_day",
    "due_label", "group", "is_recurring",
}


def set_tz(name):
    os.environ["TZ"] = name
    time.tzset()


@pytest.fixture(autouse=True)
def default_tz():
    original = os.environ.get("TZ")
    set_tz("EST5")
    yield
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


@pytest.fixture(autouse=True)
def no_real_flatpak(tmp_path_factory, monkeypatch):
    """Empty PATH: the reader's subprocess can never reach the real flatpak."""
    monkeypatch.setenv("PATH", str(tmp_path_factory.mktemp("empty-path")))


class Db:
    def __init__(self, path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=delete")
        for table, columns in (("Items", ITEM_COLUMNS),
                               ("Projects", PROJECT_COLUMNS),
                               ("Sources", SOURCE_COLUMNS)):
            self.conn.execute(f"CREATE TABLE {table} ({', '.join(columns)})")
        self.conn.execute("CREATE TABLE Sections (id, name, project_id, "
                          "is_deleted, is_archived)")
        self.project("p1")
        self.counter = 0

    def _insert(self, table, values):
        names = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        self.conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})",
                          list(values.values()))
        self.conn.commit()

    def project(self, project_id, **overrides):
        values = {"id": project_id, "name": "Project " + project_id,
                  "color": "#336699", "is_deleted": 0, "is_archived": 0}
        values.update(overrides)
        self._insert("Projects", values)

    def item(self, title, date="", recurring=False, priority=1, raw_due=None,
             **overrides):
        self.counter += 1
        due = raw_due if raw_due is not None else json.dumps(
            {"date": date, "is_recurring": recurring, "timezone": ""})
        values = {"id": f"i{self.counter}", "content": title, "due": due,
                  "project_id": "p1", "parent_id": "", "priority": priority,
                  "checked": 0, "is_deleted": 0, "is_trash": 0,
                  "item_type": "task"}
        values.update(overrides)
        self._insert("Items", values)
        return values["id"]

    def section(self, section_id, is_deleted=0, is_archived=0):
        self._insert("Sections", {"id": section_id, "name": "Section",
                                  "project_id": "p1",
                                  "is_deleted": is_deleted,
                                  "is_archived": is_archived})

    def source(self, source_type, last_sync, data="{}", is_visible=1):
        self.counter += 1
        source_id = f"s{self.counter}"
        self._insert("Sources", {"id": source_id,
                                 "source_type": source_type,
                                 "display_name": "Invented account",
                                 "is_visible": is_visible,
                                 "last_sync": last_sync, "data": data})
        return source_id


@pytest.fixture
def db(tmp_path):
    database = Db(tmp_path / "database.db")
    yield database
    database.conn.close()


def run_raw(db_path, *args, now=NOW):
    command = [sys.executable, "-I", str(SCRIPT), "--db", str(db_path)]
    if now is not None:
        command += ["--now", now]
    return subprocess.run([*command, *args], capture_output=True, text=True,
                          timeout=15, check=False)


def run(db_path, *args, now=NOW):
    result = run_raw(db_path, *args, now=now)
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1
    return json.loads(result.stdout)


def by_title(output):
    return {task["title"]: task for task in output["tasks"]}


def titles(output):
    return [task["title"] for task in output["tasks"]]


def load_module():
    spec = importlib.util.spec_from_file_location("read_tasks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Contract ---------------------------------------------------------------

def test_exact_json_contract(db):
    db.item("Task", "2026-11-25", priority=3)
    db.source("caldav", "2026-11-25T11:30:00-0500")
    output = run(db.path)
    assert set(output) == {"ok", "error", "generated_at", "counts", "tasks",
                           "sync"}
    assert output["ok"] is True and output["error"] is None
    assert output["generated_at"] == "2026-11-25T12:00:00-05:00"
    assert output["counts"] == {"overdue": 0, "today": 1, "soon": 0,
                                "important": 0}
    assert set(output["tasks"][0]) == TASK_KEYS
    assert set(output["sync"]) == {"last_sync", "age_minutes", "stale",
                                   "planify_running"}
    task = output["tasks"][0]
    assert task["project"] == "Project p1"
    assert task["project_color"] == "#336699"
    assert task["priority"] == 3


# Group boundaries --------------------------------------------------------

@pytest.mark.parametrize("date, group", [
    ("2026-11-24", "overdue"),
    ("2026-11-25", "today"),
    ("2026-11-26", "soon"),
    ("2026-11-28", "soon"),
])
def test_all_day_group_boundaries(db, date, group):
    db.item("Task", date)
    assert run(db.path)["tasks"][0]["group"] == group


def test_day_after_window_is_omitted_without_priority(db):
    db.item("Task", "2026-11-29", priority=3)
    assert run(db.path)["tasks"] == []


def test_day_after_window_is_important_with_priority(db):
    db.item("Task", "2026-11-29", priority=4)
    assert run(db.path)["tasks"][0]["group"] == "important"


def test_no_due_priority_is_important(db):
    db.item("Urgent", "", priority=4)
    db.item("Normal", "", priority=3)
    output = run(db.path)
    assert titles(output) == ["Urgent"]
    assert output["tasks"][0]["group"] == "important"
    assert output["tasks"][0]["due"] is None
    assert output["tasks"][0]["due_label"] == ""
    assert output["tasks"][0]["all_day"] is False


def test_high_priority_stays_in_most_urgent_group(db):
    db.item("Late", "2026-11-20", priority=4)
    db.item("Now", "2026-11-25", priority=4)
    db.item("Next", "2026-11-26", priority=4)
    output = run(db.path)
    assert [t["group"] for t in output["tasks"]] == ["overdue", "today", "soon"]
    assert output["counts"] == {"overdue": 1, "today": 1, "soon": 1,
                                "important": 0}


def test_timed_task_earlier_today_stays_today(db):
    db.item("Earlier", "2026-11-25T08:00:00-0500")
    task = run(db.path)["tasks"][0]
    assert task["group"] == "today"
    assert task["due_label"] == "Today 8:00 AM"


@pytest.mark.parametrize("date, group", [
    ("2026-11-24T23:59:59-0500", "overdue"),
    ("2026-11-25T00:00:00-0500", "today"),
    ("2026-11-25T23:59:59-0500", "today"),
    ("2026-11-26T00:00:00-0500", "soon"),
    ("2026-11-28T23:59:59-0500", "soon"),
])
def test_midnight_boundaries(db, date, group):
    db.item("Task", date)
    assert run(db.path)["tasks"][0]["group"] == group


def test_midnight_boundary_after_window(db):
    db.item("Task", "2026-11-29T00:00:00-0500")
    assert run(db.path)["tasks"] == []


@pytest.mark.parametrize("now, group", [
    ("2026-11-25T23:59:59-05:00", "today"),
    ("2026-11-26T00:00:00-05:00", "overdue"),
])
def test_now_crossing_midnight(db, now, group):
    db.item("Task", "2026-11-25")
    assert run(db.path, now=now)["tasks"][0]["group"] == group


# Timezones ---------------------------------------------------------------

def test_offset_moves_task_to_previous_local_date(db):
    # 02:00 UTC on the 26th is 21:00 on the 25th in EST.
    db.item("Task", "2026-11-26T02:00:00+0000")
    task = run(db.path)["tasks"][0]
    assert task["group"] == "today"
    assert task["due"] == "2026-11-25T21:00:00-05:00"
    assert task["due_label"] == "Today 9:00 PM"


def test_offset_moves_task_to_next_local_date(db):
    set_tz("JST-9")
    db.item("Task", "2026-11-25T20:00:00-0500")  # 10:00 on the 26th in JST.
    task = run(db.path, now="2026-11-25T12:00:00+09:00")["tasks"][0]
    assert task["group"] == "soon"
    assert task["due"] == "2026-11-26T10:00:00+09:00"
    assert task["due_label"] == "Tomorrow 10:00 AM"


def test_z_suffix(db):
    db.item("Task", "2026-11-25T04:59:59Z")  # 23:59:59 on the 24th in EST.
    assert run(db.path)["tasks"][0]["group"] == "overdue"


def test_naive_datetime_is_local(db):
    db.item("Task", "2026-11-25T23:30:00")
    task = run(db.path)["tasks"][0]
    assert task["group"] == "today"
    assert task["due"] == "2026-11-25T23:30:00-05:00"


def test_now_is_converted_to_local_date(db):
    db.item("Task", "2026-11-25")
    # 03:00 UTC on the 26th is still the 25th in EST.
    output = run(db.path, now="2026-11-26T03:00:00Z")
    assert output["tasks"][0]["group"] == "today"


def test_dst_transition_uses_local_offset(db):
    set_tz("EST5EDT,M3.2.0,M11.1.0")
    db.item("Task", "2026-07-01T03:30:00+0000")  # 23:30 June 30 in EDT.
    task = run(db.path, now="2026-06-30T12:00:00-04:00")["tasks"][0]
    assert task["group"] == "today"
    assert task["due"] == "2026-06-30T23:30:00-04:00"


# Sorting -----------------------------------------------------------------

def test_all_day_sorts_before_timed_on_same_date(db):
    db.item("Timed", "2026-11-25T00:00:00-0500", priority=4)
    db.item("All day", "2026-11-25", priority=1)
    assert titles(run(db.path)) == ["All day", "Timed"]


def test_sort_by_group_due_priority_title(db):
    db.item("Important", "", priority=4)
    db.item("Soon", "2026-11-27")
    db.item("Today b", "2026-11-25", priority=2)
    db.item("Today a", "2026-11-25", priority=2)
    db.item("Today high", "2026-11-25", priority=4)
    db.item("Today timed late", "2026-11-25T18:00:00-0500", priority=4)
    db.item("Today timed early", "2026-11-25T09:00:00-0500")
    db.item("Overdue", "2026-11-01")
    assert titles(run(db.path)) == [
        "Overdue", "Today high", "Today a", "Today b", "Today timed early",
        "Today timed late", "Soon", "Important",
    ]


def test_important_no_due_sorts_last(db):
    db.item("No due", "", priority=4)
    db.item("Far", "2027-01-15", priority=4)
    assert titles(run(db.path)) == ["Far", "No due"]


def test_timed_sort_uses_instant_across_offsets(db):
    db.item("Second", "2026-11-25T11:00:00-0500")  # 16:00 UTC.
    db.item("First", "2026-11-25T15:00:00+0000")  # 15:00 UTC.
    assert titles(run(db.path)) == ["First", "Second"]


# Fields ------------------------------------------------------------------

@pytest.mark.parametrize("recurring", [True, False])
def test_recurring_flag(db, recurring):
    db.item("Task", "2026-11-25", recurring=recurring)
    assert run(db.path)["tasks"][0]["is_recurring"] is recurring


@pytest.mark.parametrize("date, label", [
    ("2026-11-25", "Today"),
    ("2026-11-25T15:00:00-0500", "Today 3:00 PM"),
    ("2026-11-25T00:05:00-0500", "Today 12:05 AM"),
    ("2026-11-26", "Tomorrow"),
    ("2026-11-26T12:00:00-0500", "Tomorrow 12:00 PM"),
    ("2026-11-27", "Fri"),
    ("2026-11-24", "Yesterday"),
    ("2026-11-22", "3 days ago"),
    ("2026-11-01", "Nov 1"),
])
def test_due_labels(db, date, label):
    db.item("Task", date, priority=4)
    assert run(db.path)["tasks"][0]["due_label"] == label


def test_due_labels_far_future(db):
    db.item("Same year", "2026-12-30", priority=4)
    db.item("Next year", "2027-01-15", priority=4)
    tasks = by_title(run(db.path))
    assert tasks["Same year"]["due_label"] == "Dec 30"
    assert tasks["Next year"]["due_label"] == "Jan 15, 2027"


# Excluded states ---------------------------------------------------------

@pytest.mark.parametrize("field", ["checked", "is_deleted", "is_trash"])
def test_excluded_item_states(db, field):
    db.item("Task", "2026-11-25", **{field: 1})
    assert run(db.path)["tasks"] == []


@pytest.mark.parametrize("field", ["is_deleted", "is_archived"])
def test_excluded_project_states(db, field):
    db.project("p2", **{field: 1})
    db.item("Task", "2026-11-25", project_id="p2")
    assert run(db.path)["tasks"] == []


def test_missing_project_excluded(db):
    db.item("Task", "2026-11-25", project_id="gone")
    assert run(db.path)["tasks"] == []


@pytest.mark.parametrize("field", ["checked", "is_deleted", "is_trash"])
def test_child_of_closed_parent_excluded(db, field):
    parent = db.item("Parent", "", **{field: 1})
    db.item("Child", "2026-11-25", parent_id=parent)
    assert run(db.path)["tasks"] == []


def test_child_of_open_parent_included(db):
    parent = db.item("Parent", "")
    db.item("Child", "2026-11-25", parent_id=parent)
    assert titles(run(db.path)) == ["Child"]


def test_null_parent_included(db):
    db.item("Task", "2026-11-25", parent_id=None)
    assert titles(run(db.path)) == ["Task"]


# Ancestors ---------------------------------------------------------------

@pytest.mark.parametrize("field", ["checked", "is_deleted", "is_trash"])
def test_closed_grandparent_excludes_task(db, field):
    grandparent = db.item("Grandparent", "", **{field: 1})
    parent = db.item("Parent", "2026-11-25", parent_id=grandparent)
    db.item("Child", "2026-11-25", parent_id=parent)
    assert run(db.path)["tasks"] == []


def test_closed_distant_ancestor_excludes_task(db):
    current = db.item("Root", "", checked=1)
    for depth in range(6):
        current = db.item(f"Level {depth}", "2026-11-25", parent_id=current)
    assert run(db.path)["tasks"] == []


def test_open_ancestors_keep_task(db):
    grandparent = db.item("Grandparent", "")
    parent = db.item("Parent", "", parent_id=grandparent)
    db.item("Child", "2026-11-25", parent_id=parent)
    assert titles(run(db.path)) == ["Child"]


def test_missing_parent_keeps_task(db):
    db.item("Orphan", "2026-11-25", parent_id="gone")
    assert titles(run(db.path)) == ["Orphan"]


def test_missing_ancestor_ends_walk(db):
    parent = db.item("Parent", "", parent_id="gone")
    db.item("Child", "2026-11-25", parent_id=parent)
    assert titles(run(db.path)) == ["Child"]


def test_self_parent_cycle_keeps_task(db):
    db.item("Loop", "2026-11-25", id="a", parent_id="a")
    assert titles(run(db.path)) == ["Loop"]


def test_two_node_cycle_keeps_tasks(db):
    db.item("A", "2026-11-25", id="a", parent_id="b")
    db.item("B", "2026-11-25", id="b", parent_id="a")
    assert titles(run(db.path)) == ["A", "B"]


def test_task_hanging_off_cycle_is_kept(db):
    db.item("A", "", id="a", parent_id="b")
    db.item("B", "", id="b", parent_id="a")
    db.item("Tail", "2026-11-25", id="c", parent_id="a")
    assert titles(run(db.path)) == ["Tail"]


def test_closed_member_of_cycle_excludes_the_rest(db):
    db.item("A", "2026-11-25", id="a", parent_id="b")
    db.item("B", "2026-11-25", id="b", parent_id="a", checked=1)
    assert run(db.path)["tasks"] == []


# Notes -------------------------------------------------------------------

@pytest.mark.parametrize("item_type", ["note", "NOTE", "Note"])
def test_notes_excluded(db, item_type):
    db.item("Note", "2026-11-25", item_type=item_type)
    assert run(db.path)["tasks"] == []


@pytest.mark.parametrize("item_type", ["task", "TASK", "", None])
def test_task_types_included(db, item_type):
    db.item("Task", "2026-11-25", item_type=item_type)
    assert titles(run(db.path)) == ["Task"]


# Sections ----------------------------------------------------------------

@pytest.mark.parametrize("flags", [{"is_deleted": 1}, {"is_archived": 1}])
def test_closed_section_excludes_item(db, flags):
    db.section("sec1", **flags)
    db.item("Task", "2026-11-25", section_id="sec1")
    assert run(db.path)["tasks"] == []


def test_open_section_keeps_item(db):
    db.section("sec1")
    db.item("Task", "2026-11-25", section_id="sec1")
    assert titles(run(db.path)) == ["Task"]


@pytest.mark.parametrize("section_id", ["", None, "gone"])
def test_empty_or_missing_section_keeps_item(db, section_id):
    db.section("", is_deleted=1)  # An empty id must not match a closed row.
    db.item("Task", "2026-11-25", section_id=section_id)
    assert titles(run(db.path)) == ["Task"]


def test_missing_sections_table_is_reported(db):
    db.item("Task", "2026-11-25")
    db.conn.execute("DROP TABLE Sections")
    db.conn.commit()
    assert_failure(run(db.path), "database schema not recognized")


# Hidden accounts ---------------------------------------------------------

def test_hidden_source_excludes_project_items(db):
    source = db.source("caldav", "2026-11-25T11:30:00-0500", is_visible=0)
    db.project("p2", source_id=source)
    db.item("Hidden", "2026-11-25", project_id="p2")
    db.item("Shown", "2026-11-25")
    assert titles(run(db.path)) == ["Shown"]


@pytest.mark.parametrize("is_visible", [1, None])
def test_visible_source_keeps_project_items(db, is_visible):
    source = db.source("caldav", "2026-11-25T11:30:00-0500",
                       is_visible=is_visible)
    db.project("p2", source_id=source)
    db.item("Task", "2026-11-25", project_id="p2")
    assert titles(run(db.path)) == ["Task"]


@pytest.mark.parametrize("source_id", ["", None, "unmatched"])
def test_empty_or_unmatched_source_keeps_items(db, source_id):
    db.source("caldav", "2026-11-25T11:30:00-0500", is_visible=0)
    db.project("p2", source_id=source_id)
    db.item("Task", "2026-11-25", project_id="p2")
    assert titles(run(db.path)) == ["Task"]


def test_sentinel_in_hidden_source_never_output(db):
    source = db.source("caldav", "2026-11-25T11:30:00-0500", data=SECRET,
                       is_visible=0)
    db.project("p2", source_id=source)
    db.item("Task", "2026-11-25", project_id="p2")
    db.item("Other", "2026-11-25")
    result = run_raw(db.path)
    assert SECRET not in result.stdout and SECRET not in result.stderr


# Malformed due -----------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "", "not json", "{", "null", "[]", "42", '"2026-11-25"', "{}",
    '{"date": null}', '{"date": 20261125}', '{"date": "garbage"}',
    '{"date": "2026-13-45"}', '{"date": "2026-11-25T25:00:00"}',
    '{"date": "2026-11-25T10:00:00+9900"}', '{"date": "0001-01-01T00:00:00+1400"}',
    "[" * 100000,
])
def test_malformed_due_is_no_due_date(db, raw):
    db.item("Urgent", raw_due=raw, priority=4)
    db.item("Normal", raw_due=raw, priority=1)
    output = run(db.path)
    assert output["ok"] is True
    assert titles(output) == ["Urgent"]
    assert output["tasks"][0]["group"] == "important"
    assert output["tasks"][0]["due"] is None
    assert output["tasks"][0]["is_recurring"] is False


def test_null_due_column(db):
    db.item("Urgent", priority=4, raw_due="")
    db.conn.execute("UPDATE Items SET due = NULL")
    db.conn.commit()
    assert run(db.path)["tasks"][0]["group"] == "important"


# Failure modes -----------------------------------------------------------

def assert_failure(output, error):
    assert output["ok"] is False
    assert output["error"] == error
    assert output["tasks"] == []
    assert output["counts"] == {"overdue": 0, "today": 0, "soon": 0,
                                "important": 0}
    assert output["sync"] == EMPTY_SYNC


def test_missing_db(tmp_path):
    missing = tmp_path / "private-folder" / "missing.db"
    result = run_raw(missing)
    assert result.returncode == 0
    assert str(tmp_path) not in result.stdout
    assert "private-folder" not in result.stdout
    assert_failure(json.loads(result.stdout), "database not found")
    assert not missing.exists()


def test_locked_db(db):
    db.item("Task", "2026-11-25")
    locker = sqlite3.connect(db.path, isolation_level=None)
    try:
        locker.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        result = run_raw(db.path)
        elapsed = time.monotonic() - started
    finally:
        locker.execute("ROLLBACK")
        locker.close()
    assert result.returncode == 0
    assert elapsed < 6
    assert str(db.path.parent) not in result.stdout
    assert_failure(json.loads(result.stdout), "database locked")


def test_not_a_database(tmp_path):
    path = tmp_path / "junk.db"
    path.write_bytes(b"this is not sqlite" * 100)
    result = run_raw(path)
    assert result.returncode == 0
    assert str(tmp_path) not in result.stdout
    assert_failure(json.loads(result.stdout), "database unreadable")


def test_schema_mismatch(tmp_path):
    path = tmp_path / "empty.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE Unrelated (x)")
    conn.commit()
    conn.close()
    assert_failure(run(path), "database schema not recognized")


@pytest.mark.parametrize("args", [
    ("--soon-days", "abc"), ("--soon-days", "-1"), ("--min-priority", "9"),
    ("--stale-minutes", "-5"), ("--unknown",), ("--help",),
])
def test_bad_arguments_still_emit_json(db, args):
    assert_failure(run(db.path, *args), "invalid arguments")


def test_bad_now_still_emits_json(db):
    assert_failure(run(db.path, now="yesterday-ish"), "invalid arguments")


# Sync --------------------------------------------------------------------

def set_mtime(db, iso):
    stamp = datetime.fromisoformat(iso).timestamp()
    os.utime(db.path, (stamp, stamp))


def sync_for(db, running, *args):
    module = load_module()
    argv = ["--db", str(db.path), "--now", NOW, *args]
    return module.build_output(argv, detect=lambda: running)["sync"]


def fake_flatpak(tmp_path, body):
    folder = tmp_path / "fake-bin"
    folder.mkdir()
    script = folder / "flatpak"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(0o755)
    os.environ["PATH"] = str(folder)


def test_sync_reports_file_mtime_in_local_time(db):
    db.source("caldav", "")
    set_mtime(db, "2026-11-25T16:30:00+00:00")
    assert sync_for(db, True) == {
        "last_sync": "2026-11-25T11:30:00-05:00", "age_minutes": 30,
        "stale": False, "planify_running": True,
    }


@pytest.mark.parametrize("running", [True, None])
def test_old_db_not_stale_unless_planify_known_closed(db, running):
    db.source("caldav", "")
    set_mtime(db, "2026-11-20T08:00:00-05:00")
    sync = sync_for(db, running)
    assert sync["stale"] is False and sync["planify_running"] is running


def test_closed_planify_with_old_db_is_stale(db):
    db.source("caldav", "")
    set_mtime(db, "2026-11-25T10:59:00-05:00")
    sync = sync_for(db, False)
    assert sync["age_minutes"] == 61 and sync["stale"] is True


def test_age_exactly_at_threshold_is_not_stale(db):
    db.source("caldav", "")
    set_mtime(db, "2026-11-25T11:00:00-05:00")
    sync = sync_for(db, False)
    assert sync["age_minutes"] == 60 and sync["stale"] is False


def test_custom_stale_minutes(db):
    db.source("caldav", "")
    set_mtime(db, "2026-11-25T11:30:00-05:00")
    assert sync_for(db, False, "--stale-minutes", "10")["stale"] is True
    assert sync_for(db, False, "--stale-minutes", "30")["stale"] is False


def test_future_mtime_age_is_clamped(db):
    db.source("caldav", "")
    set_mtime(db, "2026-11-25T13:00:00-05:00")
    sync = sync_for(db, False)
    assert sync["age_minutes"] == 0 and sync["stale"] is False


@pytest.mark.parametrize("sources", [
    [],
    [("local", 1)],
    [("caldav", 0)],
    [("local", 1), ("caldav", 0)],
])
def test_no_visible_caldav_never_stale(db, sources):
    for source_type, is_visible in sources:
        db.source(source_type, "", is_visible=is_visible)
    set_mtime(db, "2026-01-01T00:00:00-05:00")
    sync = sync_for(db, False)
    assert sync["last_sync"] is not None and sync["stale"] is False


@pytest.mark.parametrize("is_visible", [1, None])
def test_visible_caldav_can_be_stale(db, is_visible):
    db.source("caldav", "", is_visible=is_visible)
    set_mtime(db, "2026-01-01T00:00:00-05:00")
    assert sync_for(db, False)["stale"] is True


def test_visible_caldav_counts_alongside_hidden(db):
    db.source("caldav", "", is_visible=0)
    db.source("caldav", "")
    set_mtime(db, "2026-01-01T00:00:00-05:00")
    assert sync_for(db, False)["stale"] is True


@pytest.mark.parametrize("stored", [
    "2026-11-25T11:59:00-0500", "2020-01-01T00:00:00-0500", "garbage", "", None,
])
def test_stored_last_sync_value_is_never_used(db, stored):
    db.source("caldav", stored)
    set_mtime(db, "2026-11-25T11:30:00-05:00")
    sync = sync_for(db, False)
    assert sync["last_sync"] == "2026-11-25T11:30:00-05:00"
    assert sync["age_minutes"] == 30 and sync["stale"] is False


def test_unreadable_mtime_gives_null_age_and_keeps_running(db, monkeypatch):
    db.source("caldav", "")
    module = load_module()
    monkeypatch.setattr(module, "database_mtime", lambda path: None)
    output = module.build_output(["--db", str(db.path), "--now", NOW],
                                 detect=lambda: False)
    assert output["ok"] is True
    assert output["sync"] == {"last_sync": None, "age_minutes": None,
                              "stale": False, "planify_running": False}


def test_failure_keeps_empty_sync_and_skips_detection(tmp_path):
    def must_not_run():
        raise AssertionError("detect called")
    output = load_module().build_output(
        ["--db", str(tmp_path / "missing.db"), "--now", NOW],
        detect=must_not_run)
    assert_failure(output, "database not found")


# Process detection -------------------------------------------------------

class Completed:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


@pytest.mark.parametrize("stdout, expected", [
    ("io.github.alainm23.planify\n", True),
    ("org.other.App\nio.github.alainm23.planify\n", True),
    ("", False),
    ("org.other.App\n", False),
    ("io.github.alainm23.planify.Devel\n", False),
    ("xio.github.alainm23.planify\n", False),
])
def test_detect_parses_output(monkeypatch, stdout, expected):
    module = load_module()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed(stdout=stdout)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.detect_planify_running() is expected
    command, kwargs = calls[0]
    assert command == ["flatpak", "ps", "--columns=application"]
    assert kwargs["timeout"] == 3 and kwargs["capture_output"] is True
    assert not kwargs.get("shell")


def test_detect_nonzero_exit_is_unknown(monkeypatch):
    module = load_module()
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *a, **k: Completed(1, "io.github.alainm23.planify\n"))
    assert module.detect_planify_running() is None


@pytest.mark.parametrize("error", [
    FileNotFoundError(),
    PermissionError(),
    subprocess.TimeoutExpired("flatpak", 3),
])
def test_detect_errors_are_unknown(monkeypatch, error):
    module = load_module()

    def fake_run(*args, **kwargs):
        raise error

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.detect_planify_running() is None


def test_cli_without_flatpak_is_unknown(db):
    db.source("caldav", "")
    set_mtime(db, "2026-01-01T00:00:00-05:00")
    sync = run(db.path)["sync"]
    assert sync["planify_running"] is None and sync["stale"] is False


@pytest.mark.parametrize("body, running, stale", [
    ("echo io.github.alainm23.planify", True, False),
    ("echo org.other.App", False, True),
    ("exit 1", None, False),
])
def test_cli_with_fake_flatpak(db, tmp_path, body, running, stale):
    db.source("caldav", "")
    set_mtime(db, "2026-01-01T00:00:00-05:00")
    fake_flatpak(tmp_path, body)
    sync = run(db.path)["sync"]
    assert sync["planify_running"] is running and sync["stale"] is stale


# Colors ------------------------------------------------------------------

@pytest.mark.parametrize("color", [
    "#abc", "#ABC", "#336699", "#AbCdEf", "#80336699",
])
def test_valid_hex_colors_pass_through(db, color):
    db.project("p2", color=color)
    db.item("Task", "2026-11-25", project_id="p2")
    assert run(db.path)["tasks"][0]["project_color"] == color


@pytest.mark.parametrize("name, expected", [
    ("berry_red", "#b8256f"), ("red", "#db4035"), ("orange", "#ff9933"),
    ("yellow", "#fad000"), ("olive_green", "#afb83b"),
    ("lime_green", "#7ecc49"), ("green", "#299438"),
    ("mint_green", "#6accbc"), ("teal", "#158fad"), ("sky_blue", "#14aaf5"),
    ("light_blue", "#96c3eb"), ("blue", "#4073ff"), ("grape", "#884dff"),
    ("violet", "#af38eb"), ("lavender", "#eb96eb"), ("magenta", "#e05194"),
    ("salmon", "#ff8d85"), ("charcoal", "#808080"), ("grey", "#b8b8b8"),
    ("taupe", "#ccac93"),
])
def test_named_colors(name, expected):
    module = load_module()
    assert module.normalize_color(name) == expected
    assert module.normalize_color(name.upper()) == expected
    assert module.normalize_color(name.title()) == expected


@pytest.mark.parametrize("value", [
    "", None, 42, "gray", "purple", "#", "#ab", "#abcd", "#abcde", "#abcdefa",
    "#abcdefabc", "#ggg", "336699", " red", "red ", "#336699\n",
    "url(evil)", b"\xff",
])
def test_invalid_colors_become_empty(value):
    assert load_module().normalize_color(value) == ""


def test_invalid_color_in_output_is_empty(db):
    db.project("p2", color="not-a-color")
    db.item("Task", "2026-11-25", project_id="p2")
    assert run(db.path)["tasks"][0]["project_color"] == ""


# Options -----------------------------------------------------------------

def test_custom_soon_days(db):
    db.item("Inside", "2026-11-30")
    db.item("Outside", "2026-12-01")
    assert titles(run(db.path, "--soon-days", "5")) == ["Inside"]


def test_soon_days_zero(db):
    db.item("Today", "2026-11-25")
    db.item("Tomorrow", "2026-11-26")
    assert titles(run(db.path, "--soon-days", "0")) == ["Today"]


def test_custom_min_priority(db):
    db.item("P4", "", priority=4)
    db.item("P3", "", priority=3)
    db.item("P2", "", priority=2)
    assert titles(run(db.path, "--min-priority", "3")) == ["P4", "P3"]


# Security and read-only --------------------------------------------------

def test_sentinel_in_sources_never_output(db):
    db.source("caldav", "2026-11-25T11:30:00-0500",
              data=json.dumps({"password": SECRET}))
    db.item("Task", "2026-11-25")
    result = run_raw(db.path)
    assert SECRET not in result.stdout and SECRET not in result.stderr


def test_sources_data_is_never_queried(db, monkeypatch):
    db.source("caldav", "2026-11-25T11:30:00-0500", data=SECRET)
    db.item("Task", "2026-11-25")
    module = load_module()
    statements = []
    real_connect = sqlite3.connect

    def traced(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(module.sqlite3, "connect", traced)
    rows, has_caldav, _ = module.read_database(str(db.path))
    assert rows and has_caldav
    joined = "\n".join(statements)
    assert "Sources" in joined
    assert not re.search(r"\bdata\b", joined, re.IGNORECASE)
    assert "last_sync" not in joined
    assert "*" not in joined
    assert SECRET not in repr(rows)


def test_connection_is_read_only(db):
    module = load_module()
    conn = module.open_readonly(str(db.path))
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO Projects (id) VALUES ('nope')")
    finally:
        conn.close()


@pytest.mark.parametrize("name", [
    "with space.db", "q?mark.db", "hash#tag.db", "per%20cent.db",
    "amp&mode=rwc.db", "unié.db",
])
def test_uri_quoting_of_special_paths(tmp_path, name):
    database = Db(tmp_path / name)
    try:
        database.item("Task", "2026-11-25")
        assert titles(run(database.path)) == ["Task"]
    finally:
        database.conn.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]


def test_reader_leaves_db_bytes_unchanged(db):
    db.item("Task", "2026-11-25")
    db.source("caldav", "2026-11-25T11:30:00-0500", data=SECRET)
    db.conn.close()
    before = hashlib.sha256(db.path.read_bytes()).hexdigest()
    mtime = db.path.stat().st_mtime_ns
    listing = sorted(p.name for p in db.path.parent.iterdir())
    for _ in range(3):
        assert run(db.path)["ok"] is True
    assert hashlib.sha256(db.path.read_bytes()).hexdigest() == before
    assert db.path.stat().st_mtime_ns == mtime
    assert sorted(p.name for p in db.path.parent.iterdir()) == listing


def test_source_has_no_hard_coded_home_or_star_select():
    source = SCRIPT.read_text()
    assert "/home/" not in source
    assert not re.search(r"select\s+\*", source, re.IGNORECASE)
