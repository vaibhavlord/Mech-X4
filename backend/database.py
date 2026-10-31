"""Small SQLite helpers; resolve TRUSTMESH_DB_PATH on every connection."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def get_connection():
    """Open a configured database, commit on success, and always close it."""
    path = Path(os.environ.get("TRUSTMESH_DB_PATH", "data/trustmesh.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database():
    """Create the MVP tables without changing existing data."""
    with get_connection() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS projects "
            "(id TEXT PRIMARY KEY, name TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS trusts "
            "(id INTEGER PRIMARY KEY, source_project TEXT, target_project TEXT, "
            "UNIQUE(source_project, target_project))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS signals "
            "(id INTEGER PRIMARY KEY, project_id TEXT, contributor_id TEXT, "
            "signal_type TEXT CHECK(signal_type IN ('vouch', 'denounce')), "
            "reason TEXT)"
        )


def seed_demo():
    """Insert the demo once, preserving any existing projects and signals."""
    projects = [
        ("project-a", "Project A"),
        ("project-b", "Project B"),
        ("project-c", "Project C"),
        ("project-x", "Project X"),
    ]
    signals = [
        ("project-b", "new-dev", "vouch", "Contributed a useful, reviewed fix."),
        ("project-x", "new-dev", "denounce", "Requested review of repetitive submissions."),
    ]
    with get_connection() as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO projects (id, name) VALUES (?, ?)", projects
        )
        connection.executemany(
            "INSERT OR IGNORE INTO trusts (source_project, target_project) "
            "VALUES (?, ?)",
            [("project-a", "project-b"), ("project-a", "project-c")],
        )
        for project, contributor, kind, reason in signals:
            connection.execute(
                "INSERT INTO signals (project_id, contributor_id, signal_type, reason) "
                "SELECT ?, ?, ?, ? WHERE NOT EXISTS "
                "(SELECT 1 FROM signals WHERE project_id = ? "
                "AND contributor_id = ? AND signal_type = ?)",
                (project, contributor, kind, reason, project, contributor, kind),
            )


def get_network():
    """Return deterministic, JSON-ready network data for the frontend."""
    with get_connection() as connection:
        projects = connection.execute("SELECT id, name FROM projects ORDER BY id")
        project_rows = [dict(row) for row in projects]
        trusts = connection.execute(
            'SELECT source_project AS "from", target_project AS "to" '
            "FROM trusts ORDER BY source_project, target_project"
        )
        trust_rows = [dict(row) for row in trusts]
        signals = connection.execute(
            "SELECT project_id, contributor_id, signal_type AS type, reason "
            "FROM signals ORDER BY project_id, contributor_id, signal_type, id"
        )
        return {
            "projects": project_rows,
            "trusts": trust_rows,
            "signals": [dict(row) for row in signals],
        }
