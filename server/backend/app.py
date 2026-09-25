from datetime import datetime, timezone, timedelta
from calendar import monthrange
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import base64
import csv
import io
import json
import sqlite3
import threading
import time
import urllib.error
import uuid
import shutil
import os

from pywebpush import webpush, WebPushException

from fastapi import FastAPI, HTTPException, Header, Depends, UploadFile, File, Form
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

VERSION = "1.3.1"
DB_PATH = Path("/data/ins_ei.db")
PUSHSAFER_KEY = Path("/run/secrets/pushsafer_private_key")
MANAGEMENT_KEY = Path("/run/secrets/management_api_key")
OEKOFEN_USERNAME = Path("/run/secrets/oekofen_username")
OEKOFEN_PASSWORD = Path("/run/secrets/oekofen_password")
OEKOFEN_CLIENT_SECRET = Path("/run/secrets/oekofen_client_secret")
INFLUX_TOKEN = Path("/run/secrets/influxdb_token")
INFLUX_URL = "http://ins-influxdb:8086"
INFLUX_ORG = "INS-EI"
INFLUX_BUCKET = "ins_ei"
VAPID_PRIVATE_KEY = Path("/run/secrets/vapid_private_key")
VAPID_PUBLIC_KEY = Path("/run/secrets/vapid_public_key")

OEKOFEN_TOKEN_URL = "https://my.oekofen.info/api/pwa/v1/oauth2/token"
OEKOFEN_PLANTS_URL = "https://my.oekofen.info/api/pwa/v3/plants"

app = FastAPI(
    title="INS-EI API",
    description="Backend API for INS Energy Intelligence",
    version=VERSION,
)

def require_management_api_key(x_api_key: str | None = Header(default=None)):
    expected=MANAGEMENT_KEY.read_text().strip() if MANAGEMENT_KEY.exists() else ""
    if not expected or x_api_key != expected:
        raise HTTPException(401,"Invalid management API key")
    return True

mcp = FastMCP(
    "INS-EI",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "ins-ei.ins-enertech.net",
            "ins-ei.ins-enertech.net:*",
            "127.0.0.1:*",
            "localhost:*",
        ],
        allowed_origins=[
            "https://ins-ei.ins-enertech.net",
            "https://ins-ei.ins-enertech.net:*",
            "http://127.0.0.1:*",
            "http://localhost:*",
        ],
    ),
)


@mcp.tool()
def ins_ei_status() -> dict[str, Any]:
    """Return the current status and version of the INS-EI backend."""
    return {
        "status": "ok",
        "service": "ins-ei-api",
        "version": VERSION,
    }


@mcp.tool()
def ins_ei_reminders_today() -> dict[str, Any]:
    """Return all open INS-EI reminders due today."""
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    with db() as con:
        rows = con.execute("""
            SELECT *
            FROM reminders
            WHERE status = 'open'
            ORDER BY due_at
        """).fetchall()

    result = []
    for row in rows:
        due = datetime.fromisoformat(row["due_at"]).astimezone()
        if start <= due <= end:
            result.append(dict(row))

    return {
        "period": "today",
        "count": len(result),
        "reminders": result,
    }


@mcp.tool()
def ins_ei_reminders_overdue() -> dict[str, Any]:
    """Return all overdue open INS-EI reminders."""
    now = datetime.now(timezone.utc)

    with db() as con:
        rows = con.execute("""
            SELECT *
            FROM reminders
            WHERE status = 'open'
            ORDER BY due_at
        """).fetchall()

    result = []
    for row in rows:
        due = datetime.fromisoformat(row["due_at"]).astimezone(timezone.utc)
        if due < now:
            result.append(dict(row))

    return {
        "period": "overdue",
        "count": len(result),
        "reminders": result,
    }


@mcp.tool()
def ins_ei_reminders_week() -> dict[str, Any]:
    """Return open INS-EI reminders due from today through the next six days."""
    from datetime import timedelta

    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = (
        start
        + timedelta(days=6)
    ).replace(hour=23, minute=59, second=59, microsecond=999999)

    with db() as con:
        rows = con.execute("""
            SELECT *
            FROM reminders
            WHERE status = 'open'
            ORDER BY due_at
        """).fetchall()

    result = []
    for row in rows:
        due = datetime.fromisoformat(row["due_at"]).astimezone()
        if start <= due <= end:
            result.append(dict(row))

    return {
        "period": "week",
        "count": len(result),
        "reminders": result,
    }


@mcp.tool()
def ins_ei_reminder_create(
    title: str,
    due_at: str,
    description: str | None = None,
    installation_id: str | None = None,
    customer: str | None = None,
    recurrence: str = "none",
    recurrence_timezone: str = "Europe/Vienna",
) -> dict[str, Any]:
    """Create a new INS-EI reminder, optionally recurring."""

    title = title.strip()

    if not title:
        raise ValueError("title must not be empty")

    try:
        due = datetime.fromisoformat(due_at)
    except ValueError as exc:
        raise ValueError(
            "due_at must be a valid ISO 8601 datetime"
        ) from exc

    if due.tzinfo is None:
        raise ValueError(
            "due_at must include a timezone offset"
        )

    recurrence = validate_recurrence(recurrence)

    try:
        ZoneInfo(recurrence_timezone)
    except Exception as exc:
        raise ValueError(
            f"Invalid recurrence timezone: {recurrence_timezone}"
        ) from exc

    created_at = datetime.now(timezone.utc).isoformat()

    with db() as con:
        cur = con.execute(
            """
            INSERT INTO reminders
            (
                title,
                description,
                due_at,
                installation_id,
                customer,
                created_at,
                recurrence,
                recurrence_timezone
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                description,
                due.isoformat(),
                installation_id,
                customer,
                created_at,
                recurrence,
                recurrence_timezone,
            ),
        )

        reminder_id = cur.lastrowid
        con.commit()

    return {
        "status": "created",
        "id": reminder_id,
        "title": title,
        "due_at": due.isoformat(),
        "description": description,
        "installation_id": installation_id,
        "customer": customer,
        "recurrence": recurrence,
        "recurrence_timezone": recurrence_timezone,
    }



@mcp.tool()
def ins_ei_reminder_complete(
    reminder_id: int,
) -> dict[str, Any]:
    """Complete a reminder and schedule its next recurrence if required."""

    completed_at = datetime.now(timezone.utc).isoformat()

    with db() as con:
        row = con.execute(
            """
            SELECT *
            FROM reminders
            WHERE id = ?
              AND status = 'open'
            """,
            (reminder_id,),
        ).fetchone()

        if row is None:
            raise ValueError(
                f"Open reminder with id {reminder_id} not found"
            )

        recurrence = row["recurrence"] or "none"
        recurrence_timezone = (
            row["recurrence_timezone"] or "Europe/Vienna"
        )

        if recurrence == "none":
            con.execute(
                """
                UPDATE reminders
                SET status = 'completed',
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    completed_at,
                    reminder_id,
                ),
            )

            con.commit()

            return {
                "status": "completed",
                "id": reminder_id,
                "completed_at": completed_at,
                "recurrence": "none",
            }

        next_due = next_recurrence_due(
            row["due_at"],
            recurrence,
            recurrence_timezone,
        )

        con.execute(
            """
            UPDATE reminders
            SET due_at = ?,
                completed_at = NULL,
                notified_at = NULL,
                notification_status = NULL,
                notification_error = NULL,
                status = 'open'
            WHERE id = ?
            """,
            (
                next_due.isoformat(),
                reminder_id,
            ),
        )

        con.commit()

    return {
        "status": "rescheduled",
        "id": reminder_id,
        "recurrence": recurrence,
        "next_due_at": next_due.isoformat(),
        "recurrence_timezone": recurrence_timezone,
    }



@mcp.tool()
def ins_ei_oekofen_sync() -> dict[str, Any]:
    """Synchronize all accessible OekoFEN plants and their current problems."""
    return sync_oekofen_plants()


@mcp.tool()
def ins_ei_oekofen_problem_plants() -> dict[str, Any]:
    """Return all monitored OekoFEN plants that currently have problems."""

    with db() as con:
        plants = con.execute(
            """
            SELECT *
            FROM oekofen_plants
            WHERE problem_count > 0
            ORDER BY plant_name COLLATE NOCASE
            """
        ).fetchall()

        result = []

        for plant in plants:
            problems = con.execute(
                """
                SELECT
                    problem_type,
                    message,
                    create_date
                FROM oekofen_problems
                WHERE plant_id = ?
                ORDER BY create_date DESC, id DESC
                """,
                (plant["plant_id"],),
            ).fetchall()

            result.append({
                "plant_id": plant["plant_id"],
                "plant_name": plant["plant_name"],
                "serial_number": plant["serial_number"],
                "version": plant["version"],
                "problem_count": plant["problem_count"],
                "push_enabled": bool(plant["push_enabled"]),
                "last_sync_at": plant["last_sync_at"],
                "problems": [dict(row) for row in problems],
            })

    return {
        "count": len(result),
        "plants": result,
    }


@mcp.tool()
def ins_ei_oekofen_push_set(
    plant_id: str,
    enabled: bool,
) -> dict[str, Any]:
    """Enable or disable future OekoFEN push notifications for one plant."""

    with db() as con:
        cur = con.execute(
            """
            UPDATE oekofen_plants
            SET push_enabled = ?
            WHERE plant_id = ?
            """,
            (
                1 if enabled else 0,
                plant_id,
            ),
        )

        if cur.rowcount == 0:
            raise ValueError(
                f"OekoFEN plant with id {plant_id} not found"
            )

        row = con.execute(
            """
            SELECT plant_id, plant_name, push_enabled
            FROM oekofen_plants
            WHERE plant_id = ?
            """,
            (plant_id,),
        ).fetchone()

    return {
        "plant_id": row["plant_id"],
        "plant_name": row["plant_name"],
        "push_enabled": bool(row["push_enabled"]),
    }




@mcp.tool()
def ins_ei_oekofen_analysis_set(
    plant_id: str,
    enabled: bool,
) -> dict[str, Any]:
    """Enable or disable OekoFEN historical analysis."""

    with db() as con:
        cur = con.execute(
            """
            UPDATE oekofen_plants
            SET analysis_enabled = ?
            WHERE plant_id = ?
            """,
            (1 if enabled else 0, plant_id),
        )

        if cur.rowcount == 0:
            raise ValueError(
                f"OekoFEN plant with id {plant_id} not found"
            )

        row = con.execute(
            """
            SELECT plant_id, plant_name, analysis_enabled
            FROM oekofen_plants
            WHERE plant_id = ?
            """,
            (plant_id,),
        ).fetchone()

    return {
        "plant_id": row["plant_id"],
        "plant_name": row["plant_name"],
        "analysis_enabled": bool(row["analysis_enabled"]),
    }



@mcp.tool()
def ins_ei_oekofen_analysis_plants() -> dict[str, Any]:
    """Return all OekoFEN plants enabled for analysis."""

    with db() as con:
        rows = con.execute(
            """
            SELECT plant_id, plant_name, analysis_enabled
            FROM oekofen_plants
            WHERE analysis_enabled = 1
            ORDER BY plant_name COLLATE NOCASE
            """
        ).fetchall()

    return {
        "count": len(rows),
        "plants": [dict(row) for row in rows],
    }



@mcp.tool()
def ins_ei_oekofen_multi_day_analysis(
    plant_id: str,
    end_day: str,
    days: int = 7,
) -> dict[str, Any]:
    """Analyze up to 31 days for an analysis-enabled OekoFEN plant."""

    return oekofen_multi_day_analysis(
        plant_id,
        end_day,
        days,
    )


@mcp.tool()
def ins_ei_oekofen_daily_analysis(
    plant_id: str,
    day: str,
) -> dict[str, Any]:
    """Analyze one OekoFEN daily CSV without storing its raw measurements."""

    return oekofen_daily_analysis(
        plant_id,
        day,
    )


@mcp.tool()
def ins_ei_oekofen_csv_info(
    plant_id: str,
    day: str,
) -> dict[str, Any]:
    """Download an OekoFEN daily CSV and return its structure."""

    return oekofen_csv_info(
        plant_id,
        day,
    )



@mcp.tool()
def ins_ei_tasks(
    status: str | None = None,
    project: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """List INS-EI tasks, optionally filtered by status, project/area and category."""
    with db() as con:
        sql = """SELECT t.*, c.name AS customer_name
                 FROM tasks t LEFT JOIN customers c ON c.id=t.customer_id
                 WHERE 1=1"""
        args: list[Any] = []
        if status:
            sql += " AND t.status=?"; args.append(status)
        if project:
            sql += " AND t.project_name=?"; args.append(project)
        if category:
            sql += " AND t.category=?"; args.append(category)
        sql += """ ORDER BY CASE t.priority
                   WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                   WHEN 'normal' THEN 2 ELSE 3 END,
                   COALESCE(t.due_at,'9999'), t.id"""
        rows = con.execute(sql, args).fetchall()
    return {"tasks": [dict(row) for row in rows]}


@mcp.tool()
def ins_ei_task_create(
    title: str,
    description: str | None = None,
    priority: str = "normal",
    project: str | None = None,
    category: str | None = None,
    due_at: str | None = None,
    customer_id: int | None = None,
) -> dict[str, Any]:
    """Create a task in the INS-EI service center."""
    allowed = {"low", "normal", "high", "urgent"}
    if priority not in allowed:
        return {"status": "error", "error": "invalid_priority"}
    now = datetime.now(timezone.utc).isoformat()
    with db() as con:
        cur = con.execute("""INSERT INTO tasks
          (title,description,status,priority,due_at,category,project_name,customer_id,created_at,updated_at)
          VALUES (?,?,'open',?,?,?,?,?,?,?)""",
          (title,description,priority,due_at,category,project,customer_id,now,now))
    return {"status": "created", "id": cur.lastrowid}


@mcp.tool()
def ins_ei_task_update(
    task_id: int,
    status: str | None = None,
    priority: str | None = None,
    due_at: str | None = None,
    description: str | None = None,
    project: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Update status, priority, due date or metadata of an INS-EI task."""
    with db() as con:
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return {"status": "error", "error": "task_not_found"}
        values = dict(row)
        if status is not None: values["status"] = status
        if priority is not None: values["priority"] = priority
        if due_at is not None: values["due_at"] = due_at or None
        if description is not None: values["description"] = description
        if project is not None: values["project_name"] = project
        if category is not None: values["category"] = category
        now = datetime.now(timezone.utc).isoformat()
        completed = now if values["status"] == "completed" else None
        con.execute("""UPDATE tasks SET description=?,status=?,priority=?,due_at=?,
          category=?,project_name=?,updated_at=?,completed_at=? WHERE id=?""",
          (values["description"],values["status"],values["priority"],values["due_at"],
           values["category"],values["project_name"],now,completed,task_id))
    return {"status": "updated", "id": task_id}


@mcp.tool()
def ins_ei_telemetry_status() -> dict[str, Any]:
    """Return online status of all INS-EI installations."""
    return telemetry_status()


@mcp.tool()
def ins_ei_system_info() -> dict[str, Any]:
    """Return INS-EI version, database tables and basic record counts."""

    with db() as con:
        tables = [
            row["name"]
            for row in con.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        ]

        counts = {}

        for table in (
            "customers",
            "installations",
            "devices",
            "device_integrations",
            "ins_ei_installations",
            "maintenance_records",
            "oekofen_plants",
            "oekofen_problems",
            "reminders",
        ):
            if table in tables:
                row = con.execute(
                    f"SELECT COUNT(*) AS count FROM {table}"
                ).fetchone()

                counts[table] = row["count"]

    return {
        "status": "ok",
        "version": VERSION,
        "database": str(DB_PATH),
        "tables": tables,
        "counts": counts,
    }


mcp.settings.streamable_http_path = "/"
mcp_http_app = mcp.streamable_http_app()


def require_management_key(x_ins_ei_key: str | None = Header(default=None)):
    expected = MANAGEMENT_KEY.read_text().strip()

    if not x_ins_ei_key or x_ins_ei_key != expected:
        raise HTTPException(status_code=401, detail="Invalid management API key")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def column_exists(con, table, column):
    return any(row["name"] == column for row in con.execute(f"PRAGMA table_info({table})"))


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                due_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                installation_id TEXT,
                customer TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)

        if not column_exists(con, "reminders", "notified_at"):
            con.execute("ALTER TABLE reminders ADD COLUMN notified_at TEXT")

        if not column_exists(con, "reminders", "notification_status"):
            con.execute("ALTER TABLE reminders ADD COLUMN notification_status TEXT")

        if not column_exists(con, "reminders", "notification_error"):
            con.execute("ALTER TABLE reminders ADD COLUMN notification_error TEXT")

        if not column_exists(con, "reminders", "recurrence"):
            con.execute(
                "ALTER TABLE reminders "
                "ADD COLUMN recurrence TEXT NOT NULL DEFAULT 'none'"
            )

        con.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                endpoint TEXT PRIMARY KEY,
                subscription_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        if not column_exists(con, "reminders", "recurrence_timezone"):
            con.execute(
                "ALTER TABLE reminders "
                "ADD COLUMN recurrence_timezone TEXT "
                "NOT NULL DEFAULT 'Europe/Vienna'"
            )


VALID_RECURRENCES = {"none", "daily", "weekly", "monthly"}


def validate_recurrence(recurrence: str) -> str:
    recurrence = (recurrence or "none").strip().lower()

    if recurrence not in VALID_RECURRENCES:
        raise ValueError(
            "recurrence must be one of: none, daily, weekly, monthly"
        )

    return recurrence


def next_recurrence_due(
    due_at: str,
    recurrence: str,
    recurrence_timezone: str = "Europe/Vienna",
) -> datetime:
    recurrence = validate_recurrence(recurrence)

    if recurrence == "none":
        raise ValueError("Reminder is not recurring")

    try:
        tz = ZoneInfo(recurrence_timezone)
    except Exception as exc:
        raise ValueError(
            f"Invalid recurrence timezone: {recurrence_timezone}"
        ) from exc

    current = datetime.fromisoformat(due_at).astimezone(tz)

    if recurrence == "daily":
        next_date = current.date() + timedelta(days=1)

    elif recurrence == "weekly":
        next_date = current.date() + timedelta(days=7)

    else:
        year = current.year
        month = current.month + 1

        if month == 13:
            month = 1
            year += 1

        day = min(
            current.day,
            monthrange(year, month)[1],
        )

        next_date = current.date().replace(
            year=year,
            month=month,
            day=day,
        )

    return datetime(
        next_date.year,
        next_date.month,
        next_date.day,
        current.hour,
        current.minute,
        current.second,
        current.microsecond,
        tzinfo=tz,
    )


def init_customer_db():
    """Initialize the manufacturer-neutral INS-EI customer database."""

    with db() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT,
                postal_code TEXT,
                city TEXT,
                country TEXT DEFAULT 'AT',
                phone TEXT,
                email TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        if not column_exists(con, "customers", "mobile"):
            con.execute("ALTER TABLE customers ADD COLUMN mobile TEXT")
        if not column_exists(con, "customers", "sevdesk_customer_number"):
            con.execute("ALTER TABLE customers ADD COLUMN sevdesk_customer_number TEXT")
        if not column_exists(con, "customers", "source"):
            con.execute("ALTER TABLE customers ADD COLUMN source TEXT")

        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_customers_name
            ON customers (name)
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS installations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER,
                name TEXT NOT NULL,
                installation_type TEXT DEFAULT 'heating',
                address TEXT,
                postal_code TEXT,
                city TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
            """
        )

        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_installations_customer
            ON installations (customer_id)
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                installation_id INTEGER NOT NULL,
                device_type TEXT NOT NULL,
                manufacturer TEXT,
                model TEXT,
                serial_number TEXT,
                external_id TEXT,
                online_capable INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (installation_id) REFERENCES installations(id)
            )
            """
        )

        if not column_exists(con, "devices", "construction_year"):
            con.execute("ALTER TABLE devices ADD COLUMN construction_year INTEGER")
        if not column_exists(con, "devices", "commissioning_date"):
            con.execute("ALTER TABLE devices ADD COLUMN commissioning_date TEXT")
        if not column_exists(con, "devices", "power_kw"):
            con.execute("ALTER TABLE devices ADD COLUMN power_kw REAL")
        if not column_exists(con, "devices", "touch_id"):
            con.execute("ALTER TABLE devices ADD COLUMN touch_id TEXT")

        if not column_exists(con, "devices", "maintenance_interval_months"):
            con.execute("ALTER TABLE devices ADD COLUMN maintenance_interval_months INTEGER NOT NULL DEFAULT 12")
        if not column_exists(con, "devices", "maintenance_next_due_date"):
            con.execute("ALTER TABLE devices ADD COLUMN maintenance_next_due_date TEXT")

        if not column_exists(con, "devices", "oekofen_plant_id"):
            con.execute("ALTER TABLE devices ADD COLUMN oekofen_plant_id TEXT")
        if not column_exists(con, "devices", "ins_installation_id"):
            con.execute("ALTER TABLE devices ADD COLUMN ins_installation_id TEXT")
        if not column_exists(con, "devices", "source"):
            con.execute("ALTER TABLE devices ADD COLUMN source TEXT")
        if not column_exists(con, "devices", "source_notes"):
            con.execute("ALTER TABLE devices ADD COLUMN source_notes TEXT")
        if not column_exists(con, "devices", "last_maintenance_date"):
            con.execute("ALTER TABLE devices ADD COLUMN last_maintenance_date TEXT")

        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_devices_installation
            ON devices (installation_id)
            """
        )

        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_devices_external_id
            ON devices (external_id)
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS device_integrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                integration_type TEXT NOT NULL,
                external_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                metadata TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id)
            )
            """
        )

        con.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_device_integrations_unique
            ON device_integrations (
                integration_type,
                external_id
            )
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS ins_ei_installations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                installation_id INTEGER NOT NULL UNIQUE,
                ins_installation_id TEXT,
                home_assistant INTEGER NOT NULL DEFAULT 0,
                wireguard INTEGER NOT NULL DEFAULT 0,
                telemetry INTEGER NOT NULL DEFAULT 0,
                optimizer INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (installation_id) REFERENCES installations(id)
            )
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                visit_id INTEGER,
                maintenance_id INTEGER,
                file_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                content_type TEXT,
                description TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id),
                FOREIGN KEY (visit_id) REFERENCES service_visits(id)
            )
            """
        )

        if not column_exists(con, "customer_files", "file_category"):
            con.execute("ALTER TABLE customer_files ADD COLUMN file_category TEXT NOT NULL DEFAULT 'document'")
        if not column_exists(con, "customer_files", "project_id"):
            con.execute("ALTER TABLE customer_files ADD COLUMN project_id INTEGER")
        if not column_exists(con, "customer_files", "device_id"):
            con.execute("ALTER TABLE customer_files ADD COLUMN device_id INTEGER")

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS service_visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                visit_date TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                duration_hours REAL,
                travel_km REAL,
                material TEXT,
                invoice_reference TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
            """
        )

        if not column_exists(con, "service_visits", "status"):
            con.execute("ALTER TABLE service_visits ADD COLUMN status TEXT NOT NULL DEFAULT 'planned'")
        if not column_exists(con, "service_visits", "visit_type"):
            con.execute("ALTER TABLE service_visits ADD COLUMN visit_type TEXT")
        if not column_exists(con, "service_visits", "priority"):
            con.execute("ALTER TABLE service_visits ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'")
        if not column_exists(con, "service_visits", "scheduled_at"):
            con.execute("ALTER TABLE service_visits ADD COLUMN scheduled_at TEXT")
        if not column_exists(con, "service_visits", "preparation"):
            con.execute("ALTER TABLE service_visits ADD COLUMN preparation TEXT")
        if not column_exists(con, "service_visits", "diagnosis"):
            con.execute("ALTER TABLE service_visits ADD COLUMN diagnosis TEXT")
        if not column_exists(con, "service_visits", "cause"):
            con.execute("ALTER TABLE service_visits ADD COLUMN cause TEXT")
        if not column_exists(con, "service_visits", "solution"):
            con.execute("ALTER TABLE service_visits ADD COLUMN solution TEXT")
        if not column_exists(con, "service_visits", "resolution_status"):
            con.execute("ALTER TABLE service_visits ADD COLUMN resolution_status TEXT")
        if not column_exists(con, "service_visits", "follow_up"):
            con.execute("ALTER TABLE service_visits ADD COLUMN follow_up TEXT")
        if not column_exists(con, "service_visits", "completed_at"):
            con.execute("ALTER TABLE service_visits ADD COLUMN completed_at TEXT")

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT NOT NULL DEFAULT 'normal',
                due_at TEXT,
                category TEXT,
                project_name TEXT,
                customer_id INTEGER,
                device_id INTEGER,
                visit_id INTEGER,
                maintenance_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
            """
        )

        if not column_exists(con, "tasks", "remind_at"):
            con.execute("ALTER TABLE tasks ADD COLUMN remind_at TEXT")
        if not column_exists(con, "tasks", "reminded_at"):
            con.execute("ALTER TABLE tasks ADD COLUMN reminded_at TEXT")
        if not column_exists(con, "tasks", "project_id"):
            con.execute("ALTER TABLE tasks ADD COLUMN project_id INTEGER")
        if not column_exists(con, "tasks", "scheduled_start"):
            con.execute("ALTER TABLE tasks ADD COLUMN scheduled_start TEXT")
        if not column_exists(con, "tasks", "scheduled_end"):
            con.execute("ALTER TABLE tasks ADD COLUMN scheduled_end TEXT")
        if not column_exists(con, "tasks", "outlook_event_id"):
            con.execute("ALTER TABLE tasks ADD COLUMN outlook_event_id TEXT")

        con.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                customer_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS project_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                content_type TEXT,
                description TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                device_id INTEGER,
                scheduled_at TEXT,
                status TEXT NOT NULL DEFAULT 'planned',
                burner_runtime REAL,
                average_runtime REAL,
                software_version TEXT,
                plant_online INTEGER,
                system_pressure REAL,
                remarks TEXT,
                material TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id),
                FOREIGN KEY (device_id) REFERENCES devices(id)
            )
            """
        )
        if not column_exists(con, "maintenance_jobs", "next_due_date"):
            con.execute("ALTER TABLE maintenance_jobs ADD COLUMN next_due_date TEXT")
        if not column_exists(con, "maintenance_jobs", "interval_months"):
            con.execute("ALTER TABLE maintenance_jobs ADD COLUMN interval_months INTEGER NOT NULL DEFAULT 12")
        if not column_exists(con, "maintenance_jobs", "source"):
            con.execute("ALTER TABLE maintenance_jobs ADD COLUMN source TEXT")
        if not column_exists(con, "maintenance_jobs", "source_ref"):
            con.execute("ALTER TABLE maintenance_jobs ADD COLUMN source_ref TEXT")

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                maintenance_id INTEGER NOT NULL,
                section TEXT NOT NULL,
                item_key TEXT NOT NULL,
                label TEXT NOT NULL,
                status TEXT,
                value TEXT,
                unit TEXT,
                note TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (maintenance_id) REFERENCES maintenance_jobs(id)
            )
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                installation_id INTEGER NOT NULL,
                device_id INTEGER,
                maintenance_date TEXT NOT NULL,
                maintenance_type TEXT,
                operating_hours REAL,
                description TEXT,
                measurements TEXT,
                next_due_date TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (installation_id) REFERENCES installations(id),
                FOREIGN KEY (device_id) REFERENCES devices(id)
            )
            """
        )

        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_maintenance_installation
            ON maintenance_records (installation_id)
            """
        )


        con.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                installation_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                received_at TEXT NOT NULL,
                data_json TEXT NOT NULL
            )
            """
        )

        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_telemetry_samples_installation_time
            ON telemetry_samples (
                installation_id,
                timestamp
            )
            """
        )


        con.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_installations (
                installation_id TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_data_at TEXT,
                sample_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def init_oekofen_db():
    with db() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS oekofen_plants (
                plant_id TEXT PRIMARY KEY,
                plant_name TEXT,
                serial_number TEXT,
                version TEXT,
                problem_count INTEGER NOT NULL DEFAULT 0,
                push_enabled INTEGER NOT NULL DEFAULT 1,
                first_seen_at TEXT NOT NULL,
                last_sync_at TEXT NOT NULL
            )
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS oekofen_problems (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plant_id TEXT NOT NULL,
                problem_type TEXT,
                message TEXT,
                create_date TEXT,
                synced_at TEXT NOT NULL
            )
            """
        )

        con.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_oekofen_problems_plant_id
            ON oekofen_problems (plant_id)
            """
        )


def oekofen_login() -> dict[str, Any]:
    username = OEKOFEN_USERNAME.read_text().strip()
    password = OEKOFEN_PASSWORD.read_text().strip()
    client_secret = OEKOFEN_CLIENT_SECRET.read_text().strip()

    payload = urlencode({
        "grant_type": "password",
        "client_id": "myoekofeninfopwa",
        "client_secret": client_secret,
        "username": username,
        "password": password,
    }).encode()

    request = Request(
        OEKOFEN_TOKEN_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode())

    if not result.get("access_token"):
        raise RuntimeError(
            "OekoFEN login returned no access_token"
        )

    return result


def oekofen_fetch_plants(
    access_token: str,
) -> list[dict[str, Any]]:
    plants = []
    offset = 0
    limit = 100

    while True:
        query = urlencode({
            "search_words": "",
            "limitstart": offset,
            "limit": limit,
            "fields": "",
        })

        url = f"{OEKOFEN_PLANTS_URL}?{query}"

        request = Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )

        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode())

        data = result.get("data")

        if isinstance(data, list):
            batch = data

        elif isinstance(data, dict):
            batch = (
                data.get("plants")
                or data.get("items")
                or data.get("data")
                or []
            )

        else:
            batch = []

        if not isinstance(batch, list):
            raise RuntimeError(
                "Unexpected OekoFEN plants response"
            )

        plants.extend(batch)

        if len(batch) < limit:
            break

        offset += limit

    return plants


def sync_oekofen_plants() -> dict[str, Any]:
    init_oekofen_db()

    auth = oekofen_login()
    access_token = auth["access_token"]

    try:
        plants = oekofen_fetch_plants(access_token)

    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise

        auth = oekofen_login()
        plants = oekofen_fetch_plants(
            auth["access_token"]
        )

    now = datetime.now(timezone.utc).isoformat()

    problem_plants = 0
    total_problems = 0

    with db() as con:
        for plant in plants:
            if not isinstance(plant, dict):
                continue

            plant_id = str(
                plant.get("id")
                or plant.get("sn")
                or ""
            ).strip()

            if not plant_id:
                continue

            plant_name = (
                plant.get("plantName")
                or plant.get("name")
                or plant_id
            )

            serial_number = str(
                plant.get("sn")
                or ""
            )

            version = str(
                plant.get("version")
                or ""
            )

            errors = plant.get("errors") or []

            if not isinstance(errors, list):
                errors = []

            problem_count = len(errors)

            if problem_count:
                problem_plants += 1
                total_problems += problem_count

            existing = con.execute(
                """
                SELECT push_enabled, first_seen_at, problem_count
                FROM oekofen_plants
                WHERE plant_id = ?
                """,
                (plant_id,),
            ).fetchone()

            previous_problem_count = int(existing["problem_count"]) if existing else 0
            is_new_problem = problem_count > 0 and previous_problem_count == 0

            if existing:
                push_enabled = existing["push_enabled"]
                first_seen_at = existing["first_seen_at"]
            else:
                push_enabled = 1
                first_seen_at = now

            con.execute(
                """
                INSERT INTO oekofen_plants (
                    plant_id,
                    plant_name,
                    serial_number,
                    version,
                    problem_count,
                    push_enabled,
                    first_seen_at,
                    last_sync_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plant_id) DO UPDATE SET
                    plant_name = excluded.plant_name,
                    serial_number = excluded.serial_number,
                    version = excluded.version,
                    problem_count = excluded.problem_count,
                    last_sync_at = excluded.last_sync_at
                """,
                (
                    plant_id,
                    plant_name,
                    serial_number,
                    version,
                    problem_count,
                    push_enabled,
                    first_seen_at,
                    now,
                ),
            )

            con.execute(
                """
                DELETE FROM oekofen_problems
                WHERE plant_id = ?
                """,
                (plant_id,),
            )

            if is_new_problem and push_enabled:
                first_problem = errors[0] if errors and isinstance(errors[0], dict) else {}
                problem_message = str(first_problem.get("message") or first_problem.get("type") or "Störung erkannt")
                try:
                    send_native_push_all(
                        "ÖkoFEN Störung · " + str(plant_name),
                        problem_message,
                        "/#oekofen",
                    )
                except Exception as exc:
                    print(f"OEKOFEN push error {plant_id}: {exc}", flush=True)

            for problem in errors:
                if not isinstance(problem, dict):
                    continue

                con.execute(
                    """
                    INSERT INTO oekofen_problems (
                        plant_id,
                        problem_type,
                        message,
                        create_date,
                        synced_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        plant_id,
                        str(problem.get("type") or ""),
                        str(problem.get("message") or ""),
                        str(problem.get("createDate") or ""),
                        now,
                    ),
                )

    return {
        "status": "ok",
        "plants": len(plants),
        "problem_plants": problem_plants,
        "problems": total_problems,
        "synced_at": now,
    }


def oekofen_fetch_csv(
    plant_id: str,
    day: str,
) -> bytes:
    """Download and decode one Pelletronic daily CSV log from OekoFEN."""

    try:
        parsed_day = datetime.strptime(
            day,
            "%Y-%m-%d",
        )
    except ValueError as exc:
        raise ValueError(
            "day must use YYYY-MM-DD format"
        ) from exc

    filename = (
        "touch_"
        + parsed_day.strftime("%Y%m%d")
        + ".csv"
    )

    remote_path = (
        "/var/log/pelletronic/"
        + filename
    )

    auth = oekofen_login()
    access_token = auth["access_token"]

    def fetch(token: str) -> bytes:
        query = urlencode({
            "path": remote_path,
        })

        url = (
            "https://my.oekofen.info"
            "/api/pwa/v1/plants/"
            + plant_id
            + "/remotecontrol/files?"
            + query
        )

        request = Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

        with urlopen(request, timeout=30) as response:
            raw_response = response.read()

        try:
            wrapper = json.loads(
                raw_response.decode("utf-8")
            )
        except Exception as exc:
            raise ValueError(
                "OekoFEN file response is not valid JSON"
            ) from exc

        if wrapper.get("status") != "success":
            raise ValueError(
                "OekoFEN file request was not successful: "
                + str(wrapper.get("message") or "unknown error")
            )

        data = wrapper.get("data")

        if not isinstance(data, dict):
            raise ValueError(
                "OekoFEN file response contains no data object"
            )

        encoded_file = data.get("file")

        if not isinstance(encoded_file, str) or not encoded_file:
            raise ValueError(
                "OekoFEN file response contains no file data"
            )

        try:
            decoded = base64.b64decode(
                encoded_file,
                validate=True,
            )
        except Exception as exc:
            raise ValueError(
                "OekoFEN file content is not valid Base64"
            ) from exc

        # The OekoFEN "size" field does not represent the
        # decoded binary file size reliably. The Base64 payload
        # itself is authoritative here.
        return decoded

    try:
        return fetch(access_token)

    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise

        auth = oekofen_login()

        return fetch(
            auth["access_token"]
        )



def decode_oekofen_csv(
    raw: bytes,
) -> tuple[str, str]:
    """Decode OekoFEN CSV using the first matching common encoding."""

    encodings = (
        "utf-8-sig",
        "utf-8",
        "cp1252",
        "latin-1",
    )

    for encoding in encodings:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue

    raise ValueError(
        "Could not decode OekoFEN CSV"
    )



def _oekofen_float(value: str) -> float | None:
    """Convert an OekoFEN CSV value to float."""
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    value = value.replace(",", ".")

    try:
        return float(value)
    except ValueError:
        return None


def _oekofen_stats(values: list[float]) -> dict[str, float | None]:
    """Return basic statistics for numeric OekoFEN values."""
    if not values:
        return {
            "min": None,
            "max": None,
            "avg": None,
        }

    return {
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "avg": round(sum(values) / len(values), 2),
    }



def oekofen_multi_day_analysis(
    plant_id: str,
    end_day: str,
    days: int = 7,
) -> dict[str, Any]:
    """Analyze several days for an analysis-enabled OekoFEN plant."""

    with db() as con:
        plant = con.execute(
            """
            SELECT plant_name, analysis_enabled
            FROM oekofen_plants
            WHERE plant_id = ?
            """,
            (plant_id,),
        ).fetchone()

    if plant is None:
        raise ValueError("OekoFEN plant not found")

    if not plant["analysis_enabled"]:
        raise ValueError(
            "Analysis is disabled for this plant"
        )

    if days < 1 or days > 31:
        raise ValueError("days must be between 1 and 31")

    end = datetime.strptime(end_day, "%Y-%m-%d")
    results = []
    errors = []

    from datetime import timedelta

    for offset in range(days - 1, -1, -1):
        day = (end - timedelta(days=offset)).strftime("%Y-%m-%d")

        try:
            results.append(
                oekofen_daily_analysis(plant_id, day)
            )
        except Exception as exc:
            errors.append({
                "day": day,
                "error": str(exc),
            })

    cycles = [
        cycle
        for result in results
        for cycle in result["burner_cycles"]["cycles"]
    ]

    starts = len(cycles)

    combustion_minutes = sum(
        cycle["combustion_minutes"] or 0
        for cycle in cycles
    )

    cycle_minutes = sum(
        cycle["duration_minutes"] or 0
        for cycle in cycles
    )

    combustion_lengths = [
        cycle["combustion_minutes"]
        for cycle in cycles
        if cycle["combustion_minutes"] is not None
    ]

    summary = {
        "burner_starts": starts,
        "total_cycle_minutes": round(cycle_minutes, 1),
        "total_combustion_minutes": round(combustion_minutes, 1),
        "avg_combustion_minutes_per_start": (
            round(combustion_minutes / starts, 1)
            if starts else None
        ),
        "shortest_combustion_minutes": (
            min(combustion_lengths)
            if combustion_lengths else None
        ),
        "longest_combustion_minutes": (
            max(combustion_lengths)
            if combustion_lengths else None
        ),
    }

    return {
        "plant_id": plant_id,
        "plant_name": plant["plant_name"],
        "summary": summary,
        "period": {
            "end_day": end_day,
            "requested_days": days,
            "analyzed_days": len(results),
        },
        "daily": results,
        "errors": errors,
    }


def oekofen_daily_analysis(
    plant_id: str,
    day: str,
) -> dict[str, Any]:
    """Analyze one OekoFEN Pelletronic daily CSV without storing raw data."""

    raw = oekofen_fetch_csv(
        plant_id,
        day,
    )

    text, encoding = decode_oekofen_csv(raw)

    csv.field_size_limit(10 * 1024 * 1024)

    reader = csv.DictReader(
        io.StringIO(text),
        delimiter=";",
    )

    # OekoFEN CSV headers may contain trailing spaces
    # (for example "Datum ", "Zeit ", "PE1_BR1 ").
    # Normalize all field names once before analysis.
    raw_rows = list(reader)

    rows = []

    for raw_row in raw_rows:
        row = {
            str(key or "").strip(): value
            for key, value in raw_row.items()
            if key is not None
        }

        if any(
            str(value or "").strip()
            for value in row.values()
        ):
            rows.append(row)

    if not rows:
        raise ValueError(
            "OekoFEN CSV contains no data rows"
        )

    def numeric(column: str) -> list[float]:
        result = []

        for row in rows:
            value = _oekofen_float(
                row.get(column)
            )

            if value is not None:
                result.append(value)

        return result

    def states(column: str) -> dict[str, int]:
        result: dict[str, int] = {}

        for row in rows:
            value = str(
                row.get(column) or ""
            ).strip()

            if not value:
                continue

            result[value] = (
                result.get(value, 0) + 1
            )

        return dict(
            sorted(
                result.items(),
                key=lambda item: (
                    -item[1],
                    item[0],
                ),
            )
        )

    temperatures = {
        "outside": _oekofen_stats(
            numeric("AT [°C]")
        ),
        "boiler": _oekofen_stats(
            numeric("KT Ist [°C]")
        ),
        "heating_circuit_1_flow": _oekofen_stats(
            numeric("HK1 VL Ist[°C]")
        ),
        "hot_water_1": _oekofen_stats(
            numeric("WW1 EinT Ist[°C]")
        ),
        "buffer_top": _oekofen_stats(
            numeric("PU1 TPO Ist[°C]")
        ),
        "buffer_middle": _oekofen_stats(
            numeric("PU1 TPM Ist[°C]")
        ),
        "pellet_boiler": _oekofen_stats(
            numeric("PE1 KT[°C]")
        ),
        "combustion_chamber": _oekofen_stats(
            numeric("PE1 FRT Ist[°C]")
        ),
    }

    modulation = _oekofen_stats(
        numeric("PE1 Modulation[%]")
    )

    pellet_level = numeric(
        "PE1 Fuellstand[kg]"
    )

    pellet_level_summary = {
        **_oekofen_stats(pellet_level),
        "first": (
            round(pellet_level[0], 2)
            if pellet_level
            else None
        ),
        "last": (
            round(pellet_level[-1], 2)
            if pellet_level
            else None
        ),
        "change": (
            round(
                pellet_level[-1]
                - pellet_level[0],
                2,
            )
            if len(pellet_level) >= 2
            else None
        ),
    }

    # Reconstruct burner cycles from the observed PE1 state sequence.
    #
    # Empirically observed on this controller:
    # 99 = idle/standby
    # 2/3 = startup / flame establishment
    # 4 = modulating combustion
    # 5 = burnout / shutdown
    #
    # State 7 is deliberately not classified yet.
    burner_states = {"2", "3", "4", "5"}

    cycles = []
    current_cycle = None

    def row_datetime(row):
        date_value = str(row.get("Datum") or "").strip()
        time_value = str(row.get("Zeit") or "").strip()

        if not date_value or not time_value:
            return None

        try:
            return datetime.strptime(
                date_value + " " + time_value,
                "%d.%m.%Y %H:%M:%S",
            )
        except ValueError:
            return None

    for row in rows:
        status = str(
            row.get("PE1 Status") or ""
        ).strip()

        timestamp = row_datetime(row)

        if status in burner_states and current_cycle is None:
            current_cycle = {
                "start": timestamp,
                "end": timestamp,
                "statuses": [],
                "combustion_start": None,
                "burnout_start": None,
            }

        if current_cycle is not None:
            if status in burner_states:
                current_cycle["end"] = timestamp

                if status not in current_cycle["statuses"]:
                    current_cycle["statuses"].append(status)

                if (
                    status == "4"
                    and current_cycle["combustion_start"] is None
                ):
                    current_cycle["combustion_start"] = timestamp

                if (
                    status == "5"
                    and current_cycle["burnout_start"] is None
                ):
                    current_cycle["burnout_start"] = timestamp

            elif status == "99":
                start = current_cycle["start"]
                end = current_cycle["end"]
                combustion_start = current_cycle["combustion_start"]
                burnout_start = current_cycle["burnout_start"]

                cycle = {
                    "start": (
                        start.isoformat()
                        if start else None
                    ),
                    "end": (
                        end.isoformat()
                        if end else None
                    ),
                    "duration_minutes": (
                        round(
                            (end - start).total_seconds() / 60,
                            1,
                        )
                        if start and end else None
                    ),
                    "statuses": current_cycle["statuses"],
                    "startup_minutes": (
                        round(
                            (
                                combustion_start - start
                            ).total_seconds() / 60,
                            1,
                        )
                        if start and combustion_start
                        else None
                    ),
                    "combustion_minutes": (
                        round(
                            (
                                burnout_start - combustion_start
                            ).total_seconds() / 60,
                            1,
                        )
                        if combustion_start and burnout_start
                        else None
                    ),
                    "burnout_minutes": (
                        round(
                            (
                                end - burnout_start
                            ).total_seconds() / 60,
                            1,
                        )
                        if burnout_start and end
                        else None
                    ),
                }

                cycles.append(cycle)
                current_cycle = None

    error_counts: dict[str, int] = {}

    for column in (
        "Fehler1",
        "Fehler2",
        "Fehler3",
    ):
        for row in rows:
            value = str(
                row.get(column) or ""
            ).strip()

            if not value:
                continue

            if value in ("0", "0.0"):
                continue

            error_counts[value] = (
                error_counts.get(value, 0) + 1
            )

    return {
        "status": "ok",
        "plant_id": plant_id,
        "day": day,
        "source": {
            "bytes": len(raw),
            "encoding": encoding,
            "rows": len(rows),
            "columns": len(
                reader.fieldnames or []
            ),
        },
        "temperatures_c": temperatures,
        "burner_cycles": {
            "count": len(cycles),
            "cycles": cycles,
            "total_cycle_minutes": round(
                sum(
                    cycle["duration_minutes"] or 0
                    for cycle in cycles
                ),
                1,
            ),
            "total_combustion_minutes": round(
                sum(
                    cycle["combustion_minutes"] or 0
                    for cycle in cycles
                ),
                1,
            ),
        },
        "pellet_boiler": {
            "modulation_percent": modulation,
            "status_values": states(
                "PE1 Status"
            ),
            "burner_signal_values": states(
                "PE1_BR1"
            ),
            "feed_motor_values": states(
                "PE1 Motor ES"
            ),
            "fan_percent": _oekofen_stats(
                numeric(
                    "PE1 Luefterdrehzahl[%]"
                )
            ),
            "draft_fan_percent": _oekofen_stats(
                numeric(
                    "PE1 Saugzugdrehzahl[%]"
                )
            ),
            "vacuum_actual": _oekofen_stats(
                numeric(
                    "PE1 Unterdruck Ist[EH]"
                )
            ),
            "pellet_level_kg": pellet_level_summary,
        },
        "heating_circuit_1": {
            "status_values": states(
                "HK1 Status"
            ),
            "pump_values": states(
                "HK1 Pumpe"
            ),
        },
        "hot_water_1": {
            "status_values": states(
                "WW1 Status"
            ),
            "pump_values": states(
                "WW1 Pumpe"
            ),
        },
        "buffer_1": {
            "status_values": states(
                "PU1 Status"
            ),
            "pump_percent": _oekofen_stats(
                numeric("PU1 Pumpe[%]")
            ),
        },
        "errors": {
            "count": sum(
                error_counts.values()
            ),
            "values": error_counts,
        },
        "note": (
            "Status codes are reported as observed values only. "
            "INS-EI does not assign semantic meanings to unknown "
            "OekoFEN status codes yet."
        ),
    }


def oekofen_csv_info(
    plant_id: str,
    day: str,
) -> dict[str, Any]:
    raw = oekofen_fetch_csv(
        plant_id,
        day,
    )

    text, encoding = decode_oekofen_csv(raw)

    csv.field_size_limit(10 * 1024 * 1024)

    reader = csv.reader(
        io.StringIO(text),
        delimiter=";",
    )

    rows = list(reader)

    if not rows:
        raise ValueError(
            "OekoFEN CSV is empty"
        )

    header = [
        column.strip()
        for column in rows[0]
    ]

    data_rows = [
        row
        for row in rows[1:]
        if any(cell.strip() for cell in row)
    ]

    return {
        "plant_id": plant_id,
        "day": day,
        "bytes": len(raw),
        "encoding": encoding,
        "delimiter": ";",
        "columns": len(header),
        "rows": len(data_rows),
        "column_names": header,
    }



def write_telemetry_to_influx(
    installation_id: str,
    timestamp: datetime,
    data: dict[str, Any],
) -> None:
    """Write one INS-EI telemetry sample to InfluxDB."""

    token = INFLUX_TOKEN.read_text().strip()

    fields = []

    for key, value in data.items():
        field = key.replace(" ", "\\ ")

        if isinstance(value, bool):
            fields.append(
                f'{field}={str(value).lower()}'
            )
        elif isinstance(value, (int, float)):
            fields.append(
                f'{field}={value}'
            )
        elif isinstance(value, str):
            escaped = value.replace(
                "\\", "\\\\"
            ).replace('"', '\\"')

            fields.append(
                f'{field}="{escaped}"'
            )

    if not fields:
        return

    tag = installation_id.replace(
        " ", "\\ "
    ).replace(",", "\\,")

    timestamp_ns = int(
        timestamp.timestamp() * 1_000_000_000
    )

    line = (
        f'ins_ei_telemetry,installation_id={tag} '
        + ",".join(fields)
        + f" {timestamp_ns}"
    )

    query = urlencode({
        "org": INFLUX_ORG,
        "bucket": INFLUX_BUCKET,
        "precision": "ns",
    })

    request = Request(
        INFLUX_URL + "/api/v2/write?" + query,
        data=line.encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
    )

    with urlopen(request, timeout=5) as response:
        response.read()




def influx_current_values(
    installation_id: str,
) -> dict[str, Any]:
    """Return latest InfluxDB values for one INS-EI installation."""

    token = INFLUX_TOKEN.read_text().strip()

    safe_id = installation_id.replace(
        '"',
        '\\"',
    )

    flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -10m)
  |> filter(fn: (r) =>
    r._measurement == "ins_ei_telemetry"
  )
  |> filter(fn: (r) =>
    r.installation_id == "{safe_id}"
  )
  |> last()
"""

    request = Request(
        INFLUX_URL + "/api/v2/query?org=" + INFLUX_ORG,
        data=json.dumps({
            "query": flux,
            "type": "flux",
        }).encode(),
        method="POST",
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
            "Accept": "application/csv",
        },
    )

    raw = urlopen(
        request,
        timeout=5,
    ).read().decode()

    values = {}

    reader = csv.DictReader(
        io.StringIO(raw)
    )

    for row in reader:
        field = row.get("_field")
        value = row.get("_value")

        if not field or field == "_field":
            continue

        if value is None:
            continue

        try:
            parsed = float(value)

            if parsed.is_integer():
                parsed = int(parsed)

            values[field] = parsed

        except ValueError:
            values[field] = value

    return values



def influx_history(
    installation_id: str,
    period: str = "24h",
) -> dict[str, Any]:
    """Return downsampled numeric telemetry history for the frontend."""

    periods = {
        "24h": ("-24h", "5m"),
        "7d": ("-7d", "30m"),
        "30d": ("-30d", "2h"),
    }

    if period not in periods:
        raise ValueError("period must be 24h, 7d or 30d")

    start, window = periods[period]
    token = INFLUX_TOKEN.read_text().strip()
    safe_id = installation_id.replace('"', '\\"')

    fields = (
        "battery.soc",
        "battery.power",
        "pv.power",
        "grid.power",
        "buffer.temperature_upper",
        "dhw.temperature",
        "weather.outdoor_temperature",
    )

    field_filter = " or ".join(
        f'r._field == "{field}"'
        for field in fields
    )

    flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: {start})
  |> filter(fn: (r) =>
    r._measurement == "ins_ei_telemetry"
  )
  |> filter(fn: (r) =>
    r.installation_id == "{safe_id}"
  )
  |> filter(fn: (r) => {field_filter})
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_field", "_value"])
"""

    request = Request(
        INFLUX_URL + "/api/v2/query?org=" + INFLUX_ORG,
        data=json.dumps({
            "query": flux,
            "type": "flux",
        }).encode(),
        method="POST",
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
            "Accept": "application/csv",
        },
    )

    raw = urlopen(request, timeout=15).read().decode()
    series = {field: [] for field in fields}

    for row in csv.DictReader(io.StringIO(raw)):
        field = row.get("_field")
        timestamp = row.get("_time")
        value = row.get("_value")

        if field not in series or not timestamp or value is None:
            continue

        try:
            numeric = round(float(value), 3)
        except ValueError:
            continue

        series[field].append({
            "time": timestamp,
            "value": numeric,
        })

    return {
        "installation_id": installation_id,
        "period": period,
        "window": window,
        "series": series,
    }


def telemetry_status() -> dict[str, Any]:
    """Return status of all INS-EI telemetry installations."""

    now = datetime.now(timezone.utc)
    installations = []

    with db() as con:
        rows = con.execute(
            """
            SELECT *
            FROM telemetry_installations
            ORDER BY installation_id
            """
        ).fetchall()

    for row in rows:
        last_seen = datetime.fromisoformat(
            row["last_seen_at"]
        )

        age_seconds = max(
            0,
            int((now - last_seen).total_seconds()),
        )

        try:
            current = influx_current_values(
                row["installation_id"]
            )
        except Exception as exc:
            print(
                "Influx read failed:",
                repr(exc),
            )
            current = {}

        installations.append({
            "installation_id": row["installation_id"],
            "current": current,
            "online": age_seconds <= 180,
            "age_seconds": age_seconds,
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "last_data_at": row["last_data_at"],
            "sample_count": row["sample_count"],
        })

    return {
        "count": len(installations),
        "online": sum(
            1 for item in installations
            if item["online"]
        ),
        "installations": installations,
    }


class TelemetryPayload(BaseModel):
    installation_id: str
    timestamp: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class PushSubscription(BaseModel):
    endpoint: str
    keys: dict[str, str]


class PushTestRequest(BaseModel):
    title: str = "INS-EI Test"
    message: str = "Push-Benachrichtigungen funktionieren."


def webpush_send(subscription: dict[str, Any], title: str, message: str):
    private_key = VAPID_PRIVATE_KEY.read_text().strip()
    payload = json.dumps({"title": title, "body": message, "url": "/"})
    return webpush(
        subscription_info=subscription,
        data=payload,
        vapid_private_key=private_key,
        vapid_claims={"sub": "mailto:ins@ins-enertech.at"},
    )

def send_native_push_all(title: str, message: str, url: str = "/") -> dict[str, int]:
    sent = 0
    failed = 0
    with db() as con:
        rows = con.execute("SELECT endpoint, subscription_json FROM push_subscriptions").fetchall()
    for row in rows:
        try:
            subscription = json.loads(row["subscription_json"])
            private_key = VAPID_PRIVATE_KEY.read_text().strip()
            webpush(
                subscription_info=subscription,
                data=json.dumps({"title": title, "body": message, "url": url}),
                vapid_private_key=private_key,
                vapid_claims={"sub": "mailto:ins@ins-enertech.at"},
            )
            sent += 1
        except Exception:
            failed += 1
    return {"sent": sent, "failed": failed}



@app.get("/api/v1/push/public-key")
def push_public_key():
    if not VAPID_PUBLIC_KEY.exists():
        raise HTTPException(503, "Push not configured")
    return {"public_key": VAPID_PUBLIC_KEY.read_text().strip()}


@app.post("/api/v1/push/subscribe")
def push_subscribe(subscription: PushSubscription):
    now = datetime.now(timezone.utc).isoformat()
    raw = subscription.model_dump()
    with db() as con:
        con.execute("""
            INSERT INTO push_subscriptions(endpoint, subscription_json, created_at, updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(endpoint) DO UPDATE SET subscription_json=excluded.subscription_json, updated_at=excluded.updated_at
        """, (subscription.endpoint, json.dumps(raw), now, now))
    return {"status": "ok"}


@app.post("/api/v1/push/test-oekofen")
def push_test_oekofen():
    result = send_native_push_all(
        "ÖkoFEN Störung · TESTANLAGE",
        "INS-EI Simulation: Teststörung erkannt.",
        "/#oekofen",
    )
    return {"status": "ok", "simulation": True, **result}


@app.post("/api/v1/push/test-task/{task_id}")
def push_test_task(task_id: int):
    with db() as con:
        row=con.execute("SELECT id,title,description FROM tasks WHERE id=?",(task_id,)).fetchone()
    if not row: raise HTTPException(404,"Task not found")
    result=send_native_push_all("Aufgabe · "+row["title"],row["description"] or "Aufgabe öffnen",f"/#tasks/task/{task_id}")
    return {"status":"ok","task_id":task_id,"url":f"/#tasks/task/{task_id}",**result}


@app.post("/api/v1/push/test")
def push_test(payload: PushTestRequest):
    sent = 0
    failed = 0
    with db() as con:
        rows = con.execute("SELECT endpoint, subscription_json FROM push_subscriptions").fetchall()
    for row in rows:
        try:
            webpush_send(json.loads(row["subscription_json"]), payload.title, payload.message)
            sent += 1
        except Exception:
            failed += 1
    return {"status": "ok", "sent": sent, "failed": failed}


class ReminderCreate(BaseModel):
    title: str
    description: str | None = None
    due_at: datetime
    installation_id: str | None = None
    customer: str | None = None
    recurrence: str = "none"
    recurrence_timezone: str = "Europe/Vienna"


def pushsafer_send(title, message):
    key = PUSHSAFER_KEY.read_text().strip()

    payload = urlencode({
        "k": key,
        "t": title,
        "m": message,
        "i": "1",
        "v": "1",
    }).encode()

    request = Request(
        "https://www.pushsafer.com/api",
        data=payload,
        method="POST",
    )

    with urlopen(request, timeout=10) as response:
        return response.read().decode()


def process_due_reminders():
    now = datetime.now(timezone.utc)

    with db() as con:
        rows = con.execute("""
            SELECT *
            FROM reminders
            WHERE status = 'open'
              AND notified_at IS NULL
            ORDER BY due_at
        """).fetchall()

    for row in rows:
        try:
            due = datetime.fromisoformat(row["due_at"])

            if due.astimezone(timezone.utc) > now:
                continue

            message = row["description"] or row["title"]

            if row["customer"]:
                message = f'{row["customer"]}: {message}'

            pushsafer_send(row["title"], message)

            notified_at = datetime.now(timezone.utc).isoformat()

            with db() as con:
                con.execute("""
                    UPDATE reminders
                    SET notified_at = ?,
                        notification_status = 'sent',
                        notification_error = NULL
                    WHERE id = ?
                      AND notified_at IS NULL
                """, (notified_at, row["id"]))

            print(f'REMINDER sent id={row["id"]} title={row["title"]}', flush=True)

        except Exception as exc:
            with db() as con:
                con.execute("""
                    UPDATE reminders
                    SET notification_status = 'error',
                        notification_error = ?
                    WHERE id = ?
                """, (str(exc)[:500], row["id"]))

            print(f'REMINDER error id={row["id"]}: {exc}', flush=True)


def process_task_reminders():
    now=datetime.now(timezone.utc)
    with db() as con:
        rows=con.execute("""SELECT t.*,c.name customer_name FROM tasks t
          LEFT JOIN customers c ON c.id=t.customer_id
          WHERE t.status!='completed' AND t.remind_at IS NOT NULL AND t.reminded_at IS NULL""").fetchall()
    for row in rows:
        try:
            remind=datetime.fromisoformat(row["remind_at"])
            if remind.tzinfo is None: remind=remind.replace(tzinfo=ZoneInfo("Europe/Vienna"))
            if remind.astimezone(timezone.utc)>now: continue
            prefix=(row["customer_name"]+": ") if row["customer_name"] else ""
            send_native_push_all("Aufgabe · "+row["title"],prefix+(row["description"] or "Erinnerung"),"/#tasks/task/"+str(row["id"]))
            with db() as con: con.execute("UPDATE tasks SET reminded_at=? WHERE id=?",(now.isoformat(),row["id"]))
        except Exception as exc:
            print(f"TASK reminder error id={row['id']}: {exc}",flush=True)


def reminder_worker():
    while True:
        try:
            process_due_reminders()
            process_task_reminders()
        except Exception as exc:
            print(f"REMINDER worker error: {exc}", flush=True)

        time.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_customer_db()
    init_oekofen_db()

    thread = threading.Thread(
        target=reminder_worker,
        daemon=True,
        name="reminder-worker",
    )
    thread.start()

    async with mcp.session_manager.run():
        yield


app.router.lifespan_context = lifespan

# Mount MCP only after the normal API routes have been registered.


@app.get("/")
def root():
    return {
        "service": "INS-EI",
        "name": "INS Energy Intelligence",
        "version": VERSION,
        "status": "running",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "ins-ei-api",
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }




@app.get("/api/v1/telemetry/history/{installation_id}")
def get_telemetry_history(
    installation_id: str,
    period: str = "24h",
):
    try:
        return influx_history(installation_id, period)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/telemetry/status")
def get_telemetry_status():
    return telemetry_status()


@app.post("/api/v1/telemetry")
def receive_telemetry(payload: TelemetryPayload):
    received_at = datetime.now(timezone.utc).isoformat()
    data_json = json.dumps(
        payload.data,
        ensure_ascii=False,
        default=str,
    )

    with db() as con:
        con.execute(
            """
            INSERT INTO telemetry_samples
            (installation_id, timestamp, received_at, data_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                payload.installation_id,
                payload.timestamp.isoformat(),
                received_at,
                data_json,
            ),
        )

        con.execute(
            """
            INSERT INTO telemetry_installations
            (installation_id, first_seen_at, last_seen_at, last_data_at, sample_count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(installation_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                last_data_at = excluded.last_data_at,
                sample_count = sample_count + 1
            """,
            (
                payload.installation_id,
                received_at,
                received_at,
                payload.timestamp.isoformat(),
            ),
        )

    try:
        write_telemetry_to_influx(
            payload.installation_id,
            payload.timestamp,
            payload.data,
        )
    except Exception as exc:
        print(
            "Influx write failed:",
            repr(exc),
        )

    return {
        "status": "stored",
        "installation_id": payload.installation_id,
        "timestamp": payload.timestamp,
        "received_points": len(payload.data),
    }


@app.post("/api/v1/reminders")
def create_reminder(
    reminder: ReminderCreate,
    _: None = Depends(require_management_key),
):
    created_at = datetime.now(timezone.utc).isoformat()

    recurrence = validate_recurrence(reminder.recurrence)

    try:
        ZoneInfo(reminder.recurrence_timezone)
    except Exception as exc:
        raise HTTPException(
            400,
            f"Invalid recurrence timezone: {reminder.recurrence_timezone}",
        ) from exc

    with db() as con:
        cur = con.execute(
            """
            INSERT INTO reminders
            (
                title,
                description,
                due_at,
                installation_id,
                customer,
                created_at,
                recurrence,
                recurrence_timezone
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reminder.title,
                reminder.description,
                reminder.due_at.isoformat(),
                reminder.installation_id,
                reminder.customer,
                created_at,
                recurrence,
                reminder.recurrence_timezone,
            ),
        )

        reminder_id = cur.lastrowid

    return {
        "status": "created",
        "id": reminder_id,
        "recurrence": recurrence,
        "recurrence_timezone": reminder.recurrence_timezone,
    }



class CustomerCreate(BaseModel):
    name: str
    address: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str = "AT"
    phone: str | None = None
    mobile: str | None = None
    email: str | None = None
    notes: str | None = None


class CustomerUpdate(CustomerCreate):
    pass


@app.put("/api/v1/customers/{customer_id}")
def update_customer(customer_id: int, customer: CustomerUpdate):
    name = customer.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    now = datetime.now(timezone.utc).isoformat()
    with db() as con:
        cur = con.execute("""
            UPDATE customers SET
                name=?, address=?, postal_code=?, city=?, country=?,
                phone=?, mobile=?, email=?, notes=?, updated_at=?
            WHERE id=?
        """, (name, customer.address, customer.postal_code, customer.city,
              customer.country, customer.phone, customer.mobile, customer.email,
              customer.notes, now, customer_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "Customer not found")
    return {"status":"updated","id":customer_id}


class CustomerImportRow(BaseModel):
    customer_number: str | None = None
    name: str
    address: str | None = None
    postal_code: str | None = None
    city: str | None = None
    phone: str | None = None
    email: str | None = None


class CustomerImportRequest(BaseModel):
    rows: list[CustomerImportRow]
    apply: bool = False


def norm_customer(value: str | None) -> str:
    import re
    text=(value or "").lower().replace("ä","ae").replace("ö","oe").replace("ü","ue").replace("ß","ss")
    # Anrede und übliche Kundenpräfixe dürfen das Matching nicht verhindern.
    text=re.sub(r"\\b(herr|frau|fam|familie|firma)\\b", " ", text)
    return re.sub(r"[^a-z0-9]", "", text)

def customer_match_score(row: CustomerImportRow, existing: dict) -> int:
    score=0
    rn,en=norm_customer(row.name),norm_customer(existing.get("name"))
    if rn and en and rn==en: score+=100
    elif rn and en and (rn in en or en in rn): score+=70
    rp,ep=norm_customer(row.postal_code),norm_customer(existing.get("postal_code"))
    rc,ec=norm_customer(row.city),norm_customer(existing.get("city"))
    ra,ea=norm_customer(row.address),norm_customer(existing.get("address"))
    postal_match=bool(rp and ep and rp==ep)
    city_match=bool(rc and ec and rc==ec)
    address_match=bool(ra and ea and (ra==ea or ra in ea or ea in ra))
    # Manche bestehende INS-EI-Kunden haben Straße/Hausnummer noch getrennt oder unvollständig.
    # Für solche Fälle vergleichen wir zusätzlich die komplette Orts-/Adresssignatur.
    import_sig=norm_customer(" ".join(filter(None,[row.address,row.postal_code,row.city])))
    existing_sig=norm_customer(" ".join(filter(None,[existing.get("address"),existing.get("postal_code"),existing.get("city")])))
    signature_match=bool(import_sig and existing_sig and (import_sig==existing_sig or import_sig in existing_sig or existing_sig in import_sig))
    if postal_match: score+=15
    if city_match: score+=10
    if address_match: score+=25
    # Gleiche Anschrift + PLZ ist bei unserem Kundenstamm ein starker Identifikator.
    # Damit matcht z.B. "Fam. Kaufmann" sicher auf "Fam. Freddy Kaufmann".
    if signature_match: score=max(score,98)
    elif address_match and postal_match: score=max(score,95)
    elif address_match and city_match: score=max(score,90)
    return score


@app.post("/api/v1/customers/import-preview")
def customer_import_preview(item: CustomerImportRequest):
    with db() as con:
        existing=[dict(x) for x in con.execute("SELECT * FROM customers").fetchall()]
    results=[]
    for row in item.rows:
        exact_no=next((x for x in existing if row.customer_number and x.get("sevdesk_customer_number")==row.customer_number),None)
        ranked=sorted(((customer_match_score(row,x),x) for x in existing),key=lambda z:z[0],reverse=True)
        best_score,best=(ranked[0] if ranked else (0,None))
        match=exact_no or (best if best_score>=85 else None)
        possible=(best if not match and best_score>=70 else None)
        action="match" if match else ("possible" if possible else "new")
        candidate=match or possible
        results.append({"source":row.model_dump(),"action":action,"score":100 if exact_no else best_score,"customer_id":candidate.get("id") if candidate else None,"existing_name":candidate.get("name") if candidate else None})
    return {"count":len(results),"new":sum(x["action"]=="new" for x in results),"matches":sum(x["action"]=="match" for x in results),"possible":sum(x["action"]=="possible" for x in results),"results":results}


@app.post("/api/v1/customers/import-apply")
def customer_import_apply(item: CustomerImportRequest):
    preview=customer_import_preview(item)["results"];now=datetime.now(timezone.utc).isoformat();created=0;updated=0
    with db() as con:
        for entry in preview:
            row=entry["source"]
            if entry["action"] in ("new","possible"):
                con.execute("""INSERT INTO customers(name,address,postal_code,city,country,phone,email,sevdesk_customer_number,source,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(row["name"],row["address"],row["postal_code"],row["city"],"AT",row["phone"],row["email"],row["customer_number"],"sevdesk-export",now,now));created+=1
            else:
                con.execute("""UPDATE customers SET address=COALESCE(NULLIF(?,''),address),postal_code=COALESCE(NULLIF(?,''),postal_code),
                  city=COALESCE(NULLIF(?,''),city),phone=COALESCE(NULLIF(?,''),phone),email=COALESCE(NULLIF(?,''),email),
                  sevdesk_customer_number=COALESCE(NULLIF(?,''),sevdesk_customer_number),updated_at=? WHERE id=?""",
                  (row["address"],row["postal_code"],row["city"],row["phone"],row["email"],row["customer_number"],now,entry["customer_id"]));updated+=1
    return {"status":"ok","created":created,"updated":updated}


class OekofenImportRow(BaseModel):
    name: str
    address: str | None = None
    postal_code: str | None = None
    city: str | None = None
    phone: str | None = None
    email: str | None = None
    plant_number: str | None = None
    model: str | None = None
    construction_year: int | None = None
    commissioning_date: str | None = None
    maintenance_2026: str | None = None
    notes: str | None = None


class OekofenImportRequest(BaseModel):
    rows: list[OekofenImportRow]


@app.post("/api/v1/oekofen/import-preview")
def oekofen_import_preview(item: OekofenImportRequest):
    with db() as con: existing=[dict(x) for x in con.execute("SELECT * FROM customers").fetchall()]
    out=[]
    for row in item.rows:
        proxy=CustomerImportRow(name=row.name,address=row.address,postal_code=row.postal_code,city=row.city,phone=row.phone,email=row.email)
        ranked=sorted(((customer_match_score(proxy,x),x) for x in existing),key=lambda z:z[0],reverse=True)
        score,best=(ranked[0] if ranked else (0,None))
        # Nur echte Zweifelsfälle müssen vom Benutzer geprüft werden.
        # >=85: sicherer Bestandskunde; <55: sicher neu; dazwischen manuell prüfen.
        action="match" if best and score>=85 else ("possible" if best and score>=55 else "new")
        out.append({"source":row.model_dump(),"action":action,"score":score,"customer_id":best.get("id") if best and score>=55 else None,"existing_name":best.get("name") if best and score>=55 else None})
    return {"count":len(out),"new":sum(x["action"]=="new" for x in out),"matches":sum(x["action"]=="match" for x in out),"possible":sum(x["action"]=="possible" for x in out),"results":out}


@app.post("/api/v1/oekofen/import-apply")
def oekofen_import_apply(item: OekofenImportRequest):
    preview=oekofen_import_preview(item)["results"];now=datetime.now(timezone.utc).isoformat();created_customers=devices=maint=0
    with db() as con:
        for e in preview:
            row=e["source"];cid=e["customer_id"]
            if not cid:
                cur=con.execute("""INSERT INTO customers(name,address,postal_code,city,country,phone,email,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",(row["name"],row["address"],row["postal_code"],row["city"],"AT",row["phone"],row["email"],"onenote-oekofen",now,now));cid=cur.lastrowid;created_customers+=1
            else:
                con.execute("""UPDATE customers SET address=COALESCE(NULLIF(?,''),address),postal_code=COALESCE(NULLIF(?,''),postal_code),city=COALESCE(NULLIF(?,''),city),phone=COALESCE(NULLIF(?,''),phone),email=COALESCE(NULLIF(?,''),email),updated_at=? WHERE id=?""",(row["address"],row["postal_code"],row["city"],row["phone"],row["email"],now,cid))
            inst=con.execute("SELECT id FROM installations WHERE customer_id=? AND installation_type='heating' ORDER BY id LIMIT 1",(cid,)).fetchone()
            if inst: iid=inst["id"]
            else:
                cur=con.execute("""INSERT INTO installations(customer_id,name,installation_type,address,postal_code,city,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",(cid,"ÖkoFEN Heizung","heating",row["address"],row["postal_code"],row["city"],row["notes"],now,now));iid=cur.lastrowid
            dev=None
            if row["plant_number"]: dev=con.execute("SELECT id FROM devices WHERE external_id=?",(row["plant_number"],)).fetchone()
            if not dev:
                cur=con.execute("""INSERT INTO devices(installation_id,device_type,manufacturer,model,external_id,construction_year,commissioning_date,source,source_notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(iid,"boiler","ÖkoFEN",row["model"],row["plant_number"],row["construction_year"],row["commissioning_date"],"onenote",row["notes"],now,now));did=cur.lastrowid;devices+=1
            else: did=dev["id"]
            if row["maintenance_2026"]:
                exists=con.execute("SELECT id FROM maintenance_jobs WHERE device_id=? AND scheduled_at=? AND source='onenote'",(did,row["maintenance_2026"])).fetchone()
                if not exists:
                    con.execute("""INSERT INTO maintenance_jobs(customer_id,device_id,scheduled_at,status,remarks,completed_at,source,source_ref,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",(cid,did,row["maintenance_2026"],"completed",row["notes"],row["maintenance_2026"],"onenote","Wartungen 2026",now,now));maint+=1
                    con.execute("UPDATE devices SET last_maintenance_date=? WHERE id=?",(row["maintenance_2026"],did))
    return {"status":"ok","created_customers":created_customers,"devices":devices,"maintenances":maint}


@app.get("/api/v1/customers")
def list_customers(q: str | None = None):
    with db() as con:
        if q:
            term = f"%{q.strip()}%"
            rows = con.execute("""
                SELECT * FROM customers
                WHERE name LIKE ? OR address LIKE ? OR postal_code LIKE ?
                   OR city LIKE ? OR phone LIKE ? OR mobile LIKE ? OR email LIKE ?
                ORDER BY name COLLATE NOCASE
            """, (term, term, term, term, term, term, term)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM customers ORDER BY name COLLATE NOCASE"
            ).fetchall()
    return {"count": len(rows), "customers": [dict(r) for r in rows]}


class DeviceCreate(BaseModel):
    manufacturer: str
    model: str | None = None
    serial_number: str | None = None
    touch_id: str | None = None
    construction_year: int | None = None
    commissioning_date: str | None = None
    power_kw: float | None = None
    maintenance_interval_months: int = 12
    maintenance_next_due_date: str | None = None
    notes: str | None = None


@app.post("/api/v1/customers/{customer_id}/devices")
def create_customer_device(customer_id: int, device: DeviceCreate):
    now = datetime.now(timezone.utc).isoformat()
    with db() as con:
        customer = con.execute("SELECT id,name FROM customers WHERE id=?", (customer_id,)).fetchone()
        if customer is None:
            raise HTTPException(404, "Customer not found")
        installation = con.execute(
            "SELECT id FROM installations WHERE customer_id=? ORDER BY id LIMIT 1",
            (customer_id,),
        ).fetchone()
        if installation is None:
            cur = con.execute("""INSERT INTO installations
                (customer_id,name,installation_type,created_at,updated_at)
                VALUES (?,?,?,?,?)""",
                (customer_id, f"Anlage {customer['name']}", "heating", now, now))
            installation_id = cur.lastrowid
        else:
            installation_id = installation["id"]
        cur = con.execute("""INSERT INTO devices
            (installation_id,device_type,manufacturer,model,serial_number,touch_id,
             construction_year,commissioning_date,power_kw,maintenance_interval_months,maintenance_next_due_date,online_capable,notes,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (installation_id,"heating",device.manufacturer,device.model,device.serial_number,
             device.touch_id,device.construction_year,device.commissioning_date,device.power_kw,
             device.maintenance_interval_months,device.maintenance_next_due_date,
             1 if device.manufacturer.lower() in ("ökofen","oekofen") else 0,
             device.notes,now,now))
        device_id=cur.lastrowid
    return {"status":"created","id":device_id,"installation_id":installation_id}


@app.put("/api/v1/devices/{device_id}")
def update_device(device_id: int, device: DeviceCreate):
    now=datetime.now(timezone.utc).isoformat()
    with db() as con:
        cur=con.execute("""UPDATE devices SET manufacturer=?,model=?,serial_number=?,touch_id=?,
            construction_year=?,commissioning_date=?,power_kw=?,maintenance_interval_months=?,
            maintenance_next_due_date=?,online_capable=?,notes=?,updated_at=? WHERE id=?""",
            (device.manufacturer,device.model,device.serial_number,device.touch_id,
             device.construction_year,device.commissioning_date,device.power_kw,
             device.maintenance_interval_months,device.maintenance_next_due_date,
             1 if device.manufacturer.lower() in ("ökofen","oekofen") else 0,
             device.notes,now,device_id))
        if cur.rowcount==0:
            raise HTTPException(404,"Device not found")
    return {"status":"updated","id":device_id}


class DeviceLinks(BaseModel):
    oekofen_plant_id: str | None = None
    ins_installation_id: str | None = None


class DeviceMaintenanceSettings(BaseModel):
    maintenance_interval_months: int
    maintenance_next_due_date: str | None = None

@app.put("/api/v1/devices/{device_id}/maintenance-settings")
def update_device_maintenance_settings(device_id: int, item: DeviceMaintenanceSettings):
    if item.maintenance_interval_months < 1:
        raise HTTPException(400,"Invalid maintenance interval")
    with db() as con:
        cur=con.execute("""UPDATE devices SET maintenance_interval_months=?,
          maintenance_next_due_date=?,updated_at=? WHERE id=?""",
          (item.maintenance_interval_months,item.maintenance_next_due_date,
           datetime.now(timezone.utc).isoformat(),device_id))
        if cur.rowcount==0: raise HTTPException(404,"Device not found")
    return {"status":"updated","id":device_id}


@app.get("/api/v1/devices/{device_id}/link-options")
def device_link_options(device_id: int):
    with db() as con:
        device=con.execute("SELECT * FROM devices WHERE id=?",(device_id,)).fetchone()
        if device is None: raise HTTPException(404,"Device not found")
        plants=con.execute("""SELECT plant_id,plant_name,serial_number,version,problem_count,last_sync_at
            FROM oekofen_plants ORDER BY plant_name COLLATE NOCASE""").fetchall()
        ins=con.execute("""SELECT installation_id,last_seen_at,last_data_at,sample_count
            FROM telemetry_installations ORDER BY installation_id COLLATE NOCASE""").fetchall()
    return {"device":dict(device),"oekofen":[dict(r) for r in plants],"ins_ei":[dict(r) for r in ins]}


@app.put("/api/v1/devices/{device_id}/links")
def update_device_links(device_id: int, links: DeviceLinks):
    with db() as con:
        cur=con.execute("""UPDATE devices SET oekofen_plant_id=?,ins_installation_id=?,updated_at=?
            WHERE id=?""",(links.oekofen_plant_id or None,links.ins_installation_id or None,
            datetime.now(timezone.utc).isoformat(),device_id))
        if cur.rowcount==0: raise HTTPException(404,"Device not found")
    return {"status":"updated","id":device_id}


@app.get("/api/v1/customers/{customer_id}/devices")
def list_customer_devices(customer_id: int):
    with db() as con:
        rows=con.execute("""SELECT d.* FROM devices d
            JOIN installations i ON i.id=d.installation_id
            WHERE i.customer_id=? ORDER BY d.id""",(customer_id,)).fetchall()
    return {"devices":[dict(r) for r in rows]}


class ServiceVisitCreate(BaseModel):
    visit_date: str
    title: str
    description: str | None = None
    duration_hours: float | None = None
    travel_km: float | None = None
    material: str | None = None
    invoice_reference: str | None = None


FILES_PATH = Path("/data/customer_files")


@app.post("/api/v1/customers/{customer_id}/files")
async def upload_customer_file(
    customer_id: int,
    file: UploadFile = File(...),
    visit_id: int | None = Form(default=None),
    maintenance_id: int | None = Form(default=None),
    description: str | None = Form(default=None),
):
    FILES_PATH.mkdir(parents=True, exist_ok=True)
    with db() as con:
        if con.execute("SELECT 1 FROM customers WHERE id=?",(customer_id,)).fetchone() is None:
            raise HTTPException(404,"Customer not found")
    suffix=Path(file.filename or "").suffix.lower()
    stored_name=f"{uuid.uuid4().hex}{suffix}"
    target=FILES_PATH/stored_name
    with target.open("wb") as out:
        shutil.copyfileobj(file.file,out)
    now=datetime.now(timezone.utc).isoformat()
    with db() as con:
        cur=con.execute("""INSERT INTO customer_files
            (customer_id,visit_id,maintenance_id,file_name,stored_name,content_type,description,created_at,file_category)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (customer_id,visit_id,maintenance_id,file.filename or stored_name,stored_name,
             file.content_type,description,now,"photo" if (file.content_type or "").startswith("image/") else "document"))
    return {"status":"stored","id":cur.lastrowid}


@app.get("/api/v1/customers/{customer_id}/files")
def list_customer_files(customer_id: int, visit_id: int | None = None):
    with db() as con:
        if visit_id is None:
            rows=con.execute("SELECT * FROM customer_files WHERE customer_id=? ORDER BY id DESC",(customer_id,)).fetchall()
        else:
            rows=con.execute("SELECT * FROM customer_files WHERE customer_id=? AND visit_id=? ORDER BY id DESC",(customer_id,visit_id)).fetchall()
    return {"files":[dict(r) for r in rows]}


@app.get("/api/v1/customer-files/{file_id}")
def get_customer_file(file_id: int):
    with db() as con:
        row=con.execute("SELECT * FROM customer_files WHERE id=?",(file_id,)).fetchone()
    if row is None: raise HTTPException(404,"File not found")
    path=FILES_PATH/row["stored_name"]
    if not path.exists(): raise HTTPException(404,"Stored file not found")
    return FileResponse(path,media_type=row["content_type"] or "application/octet-stream",headers={"Content-Disposition": f'inline; filename="{row["file_name"]}"'})


class PlannedVisitCreate(BaseModel):
    customer_id: int
    scheduled_at: str
    title: str
    visit_type: str = "service"
    priority: str = "normal"
    description: str | None = None
    preparation: str | None = None


@app.post("/api/v1/visits")
def create_planned_visit(visit: PlannedVisitCreate):
    now=datetime.now(timezone.utc).isoformat()
    visit_date=(visit.scheduled_at or "")[:10] or now[:10]
    with db() as con:
        if con.execute("SELECT 1 FROM customers WHERE id=?",(visit.customer_id,)).fetchone() is None:
            raise HTTPException(404,"Customer not found")
        cur=con.execute("""INSERT INTO service_visits
          (customer_id,visit_date,scheduled_at,title,visit_type,priority,status,
           description,preparation,created_at,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
          (visit.customer_id,visit_date,visit.scheduled_at,visit.title,visit.visit_type,
           visit.priority,"planned",visit.description,visit.preparation,now,now))
    return {"status":"created","id":cur.lastrowid}


class VisitWorkflowUpdate(BaseModel):
    status: str
    diagnosis: str | None = None
    cause: str | None = None
    solution: str | None = None
    resolution_status: str | None = None
    follow_up: str | None = None
    duration_hours: float | None = None
    material: str | None = None
    invoice_reference: str | None = None


@app.put("/api/v1/visits/{visit_id}/workflow")
def update_visit_workflow(visit_id: int, workflow: VisitWorkflowUpdate):
    allowed={"planned","in_progress","completed"}
    if workflow.status not in allowed:
        raise HTTPException(400,"Invalid visit status")
    completed_at=datetime.now(timezone.utc).isoformat() if workflow.status=="completed" else None
    with db() as con:
        cur=con.execute("""UPDATE service_visits SET status=?,diagnosis=?,cause=?,solution=?,
            resolution_status=?,follow_up=?,duration_hours=?,material=?,invoice_reference=?,
            completed_at=?,updated_at=? WHERE id=?""",
            (workflow.status,workflow.diagnosis,workflow.cause,workflow.solution,
             workflow.resolution_status,workflow.follow_up,workflow.duration_hours,
             workflow.material,workflow.invoice_reference,completed_at,
             datetime.now(timezone.utc).isoformat(),visit_id))
        if cur.rowcount==0: raise HTTPException(404,"Visit not found")
    return {"status":"updated","id":visit_id,"visit_status":workflow.status}


@app.put("/api/v1/visits/{visit_id}")
def update_planned_visit(visit_id: int, visit: PlannedVisitCreate):
    with db() as con:
        cur=con.execute("""UPDATE service_visits SET customer_id=?,visit_date=?,
          scheduled_at=?,title=?,visit_type=?,priority=?,description=?,preparation=?,updated_at=?
          WHERE id=?""",(visit.customer_id,(visit.scheduled_at or "")[:10],visit.scheduled_at,
          visit.title,visit.visit_type,visit.priority,visit.description,visit.preparation,
          datetime.now(timezone.utc).isoformat(),visit_id))
        if cur.rowcount==0: raise HTTPException(404,"Visit not found")
    return {"status":"updated","id":visit_id}


MAINTENANCE_TEMPLATE = [
("Brennraum","waermetauscher","Wärmetauscher / Federn gereinigt",None),
("Brennraum","dichtungen","Dichtungen kontrolliert",None),
("Brennraum","feuerraumfuehlerrohr","Feuerraumfühlerrohr gereinigt",None),
("Brennraum","reinigungseinrichtung","Reinigungseinrichtung kontrolliert, eingestellt und geschmiert",None),
("Brennraum","sichtkontrolle","Sichtkontrolle - sauber oder verpecht",None),
("Brenner","brennteller","Brennteller gereinigt",None),
("Brenner","brennteller_eingestellt","Brennteller eingestellt (Compact / Condens)",None),
("Brenner","rueckbrandsicherung","Rückbrandsicherung kontrolliert (Belimo)",None),
("Brenner","gluehstab","Glühstab (195 - 215 Ohm)","Ω"),
("Brenner","zuendrohr","Zündrohr gereinigt (Bohrung)",None),
("Brenner","brennerhals","Brennerhals gereinigt (Verkrustungen)",None),
("Brenner","primaer_sekundaerluft","Primär- Sekundärluft gereinigt",None),
("Brenner","flammrohr","Flammrohr / Betonteile gereinigt",None),
("Brenner","aschetransport","Aschetransport gereinigt",None),
("Brenner","brennermotor_kondensator","Brennermotor Kondensator",None),
("Brenner","radialgeblaese_kondensator","Radialgebläse Kondensator",None),
("Raumentnahme / Tagesbehälter","raumentnahmemotor","Raumentnahmemotor (Kondensator)",None),
("Raumentnahme / Tagesbehälter","saugschlaeuche","Saugschläuche Sichtkontrolle",None),
("Raumentnahme / Tagesbehälter","tagesbehaelter","Tagesbehälter entleert und gereinigt",None),
("Raumentnahme / Tagesbehälter","sieb","Sieb gereinigt",None),
("Raumentnahme / Tagesbehälter","saugturbine","Saugturbine Kohlebürsten",None),
("Kamin / Rauchrohr","saugzug","Saugzug gereinigt",None),
("Kamin / Rauchrohr","rauchgasfuehler","Rauchgasfühler gereinigt",None),
("Kamin / Rauchrohr","zugregler","Zugregler kontrolliert",None),
("Kamin / Rauchrohr","rauchrohr","Rauchrohr gereinigt",None),
("Kamin / Rauchrohr","kamin_sicht","Kamin Sichtkontrolle verpecht",None),
("Brennwert","waschduese","Waschdüse kontrolliert",None),
("Brennwert","siphon","Siphonablauf kontrolliert",None),
("Brennwert","hebepumpe","Hebepumpe gereinigt (Störkontakt)",None),
("Probelauf / Funktionstest","reinigung","Reinigung",None),
("Probelauf / Funktionstest","rueckbrandsicherung_test","Rückbrandsicherung",None),
("Probelauf / Funktionstest","saugzug_test","Saugzug / Radialgebläse",None),
("Probelauf / Funktionstest","ascheaustragung","Ascheaustragung",None),
("Probelauf / Funktionstest","unterdruck","Unterdruck kontrollieren",None),
]

class MaintenanceCreate(BaseModel):
    customer_id: int
    device_id: int | None = None
    scheduled_at: str | None = None
    interval_months: int = 12

@app.get("/api/v1/maintenance-due")
def maintenance_due():
    today=datetime.now(timezone.utc).date()
    soon=today+timedelta(days=45)
    with db() as con:
        rows=con.execute("""SELECT d.id device_id,d.manufacturer,d.model,d.serial_number,
          d.maintenance_interval_months,d.maintenance_next_due_date,
          i.customer_id,c.name customer_name,c.city customer_city
          FROM devices d JOIN installations i ON i.id=d.installation_id
          JOIN customers c ON c.id=i.customer_id
          WHERE d.maintenance_next_due_date IS NOT NULL
          ORDER BY d.maintenance_next_due_date""").fetchall()
    result=[]
    for r in rows:
        x=dict(r)
        try: due=date.fromisoformat(x["maintenance_next_due_date"])
        except Exception: continue
        x["due_state"]="overdue" if due<today else "soon" if due<=soon else "future"
        x["days_until"]=(due-today).days
        result.append(x)
    return {"items":result}


class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    status: str = "open"
    priority: str = "normal"
    due_at: str | None = None
    category: str | None = None
    project_name: str | None = None
    project_id: int | None = None
    customer_id: int | None = None
    remind_at: str | None = None
    scheduled_start: str | None = None
    scheduled_end: str | None = None
    outlook_event_id: str | None = None

@app.post("/api/v1/tasks/seed-ins-ei-backlog")
def seed_ins_ei_backlog():
    backlog=[
      ("PWA + Offline-Grundgerüst","Servicezentrale installierbar machen; Kunden, Anlagen, Einsätze und Wartungen offline verfügbar; lokale Änderungen und Fotos synchronisieren.","urgent","PWA / Offline"),
      ("Offline-Sync für Einsätze und Störungen","Störungseinsätze im Keller vollständig offline dokumentieren und später automatisch synchronisieren.","urgent","PWA / Offline"),
      ("Offline-Sync für Wartungen","Wartungscheckliste, Messwerte, Material und Fotos ohne Empfang erfassen und später synchronisieren.","urgent","PWA / Offline"),
      ("Web Push Benachrichtigungen","PWA Push für Termine, Wartungen, Aufgaben und Anlagenwarnungen; Pushsafer erst nach erfolgreichem Praxistest ablösen.","high","Benachrichtigungen"),
      ("Login und Sicherheit","Sichere Anmeldung, Sessions und Schutz der Servicezentrale; Grundlage für Handy/Tablet und verschlüsselte Zugangsdaten.","high","Sicherheit"),
      ("Kunden-Projekte","Projektstruktur Kunde → Projekt → Anlagen/Einsätze/Aufgaben/Fotos/Dokumente; Kaufmann Umbau 2026 als Pilot.","high","Projekte"),
      ("Wartungsmodul fertigstellen","Fälligkeiten, Vorwartungswerte, Fotos, Abschluss, Historie und Praxistest mit den nächsten echten Wartungen.","high","Wartungen"),
      ("Kalender / Outlook Synchronisation","Einsätze, Wartungen und Aufgaben mit Servicekalender bzw. Outlook verbinden; Änderungen und Erinnerungen berücksichtigen.","high","Kalender"),
      ("Erinnerungen in INS-EI","Aufgaben-, Einsatz- und Wartungserinnerungen zentral verwalten und später per Web Push zustellen.","high","Erinnerungen"),
      ("sevdesk Read-only Finanzcheck fertigstellen","Finanzdaten, offene Forderungen/Verbindlichkeiten, Liquidität und Kennzahl zur möglichen Privatentnahme weiter automatisieren.","normal","sevdesk"),
      ("sevdesk Schreibintegration","Angebote/Rechnungen bzw. Verknüpfungen aus Kunde, Projekt und Einsatz vorbereiten; erst nach stabiler Read-only-Integration.","normal","sevdesk"),
      ("ChatGPT ↔ INS-EI API","Kunden, Anlagen, Projekte, Einsätze, Wartungen und Aufgaben aus ChatGPT lesen und später kontrolliert schreiben können.","high","ChatGPT"),
      ("Beschaffung / Nachbestellung","Verbrauchs- und Kleinmaterial direkt aus Einsatz/Wartung zur Nachbestellung markieren.","normal","Material"),
      ("Lagerverwaltung","Bestände, Verbrauch und Nachbestellung für häufig benötigtes Material aufbauen.","normal","Lager"),
      ("Kunden- und ÖkoFEN-Import","Bestehende Kunden gesammelt übernehmen und ÖkoFEN-Anlagen möglichst automatisch über Kesselnummer/Touch-ID zuordnen.","normal","Kunden"),
      ("Dokumentenverwaltung ausbauen","Fotos/PDF/Word zentral pro Kunde speichern und mit Anlage, Projekt, Einsatz und Wartung verknüpfen.","normal","Dokumente"),
      ("Zugangsdaten-Tresor","Kundenzugangsdaten verschlüsselt speichern und im Frontend nur gezielt anzeigen.","normal","Sicherheit"),
      ("Service-Dashboard","Heute/nächste Einsätze, fällige Wartungen, offene Aufgaben, Folgearbeiten und wichtige Warnungen zusammenfassen.","normal","Servicezentrale"),
      ("INS-EI Energy / Thermal Shadow weiterentwickeln","Niki-Home, JoWu und Kaufmann weiter validieren; Forecast, Preise, Optimierung und nachvollziehbare Einsparungen ausbauen.","high","Energy / Thermal Shadow"),
      ("Kaufmann Pilotprojekt weiterführen","Umbau dokumentieren und anschließend INS-EI Energy/Service inkl. Brennstoffverbrauch und Einsparungsnachweis weiterführen.","high","Pilot Kaufmann"),
      ("JoWu Pilot weiterführen","Datenzugriff KNV, Verbrauchserfassung und INS-EI Aufzeichnung/Analyse weiterführen.","normal","Pilot JoWu"),
      ("Deployment, Backup und Restore härten","Deploy-Prüfungen erweitern, echten API-Starttest ergänzen und Backup/Restore für Datenbank und Kundendateien absichern.","high","Betrieb")
    ]
    now=datetime.now(timezone.utc).isoformat()
    inserted=0
    with db() as con:
        for title,description,priority,category in backlog:
            exists=con.execute("SELECT 1 FROM tasks WHERE title=? AND project_name='INS-EI Entwicklung'",(title,)).fetchone()
            if exists: continue
            con.execute("""INSERT INTO tasks
              (title,description,status,priority,category,project_name,created_at,updated_at)
              VALUES (?,?,'open',?,?, 'INS-EI Entwicklung',?,?)""",
              (title,description,priority,category,now,now))
            inserted+=1
    return {"status":"ok","inserted":inserted,"total":len(backlog)}


@app.get("/api/v1/chatgpt/tasks", dependencies=[Depends(require_management_api_key)])
def chatgpt_list_tasks(status: str | None = None, project: str | None = None, category: str | None = None):
    with db() as con:
        sql="""SELECT t.*,c.name customer_name FROM tasks t LEFT JOIN customers c ON c.id=t.customer_id WHERE 1=1"""
        args=[]
        if status: sql+=" AND t.status=?";args.append(status)
        if project: sql+=" AND t.project_name=?";args.append(project)
        if category: sql+=" AND t.category=?";args.append(category)
        sql+=" ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,COALESCE(t.due_at,'9999')"
        rows=con.execute(sql,args).fetchall()
    return {"tasks":[dict(r) for r in rows]}

@app.post("/api/v1/chatgpt/tasks", dependencies=[Depends(require_management_api_key)])
def chatgpt_create_task(item: TaskCreate):
    return create_task(item)

@app.put("/api/v1/chatgpt/tasks/{task_id}", dependencies=[Depends(require_management_api_key)])
def chatgpt_update_task(task_id: int, item: TaskCreate):
    return update_task(task_id,item)


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    status: str = "active"
    customer_id: int | None = None


@app.get("/api/v1/projects")
def list_projects():
    with db() as con:
        rows=con.execute("""SELECT p.*,c.name customer_name,
          (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.status!='completed') open_tasks,
          (SELECT COUNT(*) FROM project_files f WHERE f.project_id=p.id) file_count
          FROM projects p LEFT JOIN customers c ON c.id=p.customer_id
          ORDER BY CASE p.status WHEN 'active' THEN 0 ELSE 1 END,p.name COLLATE NOCASE""").fetchall()
    return {"projects":[dict(r) for r in rows]}


@app.post("/api/v1/projects")
def create_project(item: ProjectCreate):
    now=datetime.now(timezone.utc).isoformat()
    with db() as con:
        cur=con.execute("""INSERT INTO projects(name,description,status,customer_id,created_at,updated_at)
          VALUES(?,?,?,?,?,?)""",(item.name,item.description,item.status,item.customer_id,now,now))
    return {"status":"created","id":cur.lastrowid}


@app.put("/api/v1/projects/{project_id}")
def update_project(project_id: int, item: ProjectCreate):
    now=datetime.now(timezone.utc).isoformat()
    completed=now if item.status=="completed" else None
    with db() as con:
        cur=con.execute("""UPDATE projects SET name=?,description=?,status=?,customer_id=?,
          updated_at=?,completed_at=? WHERE id=?""",
          (item.name,item.description,item.status,item.customer_id,now,completed,project_id))
        if cur.rowcount==0: raise HTTPException(404,"Project not found")
    return {"status":"updated","id":project_id}


@app.get("/api/v1/projects/{project_id}")
def get_project(project_id: int):
    with db() as con:
        p=con.execute("""SELECT p.*,c.name customer_name FROM projects p
          LEFT JOIN customers c ON c.id=p.customer_id WHERE p.id=?""",(project_id,)).fetchone()
        if not p: raise HTTPException(404,"Project not found")
        tasks=con.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY status,COALESCE(due_at,'9999')",(project_id,)).fetchall()
        files=con.execute("SELECT * FROM project_files WHERE project_id=? ORDER BY created_at DESC",(project_id,)).fetchall()
    return {**dict(p),"tasks":[dict(x) for x in tasks],"files":[dict(x) for x in files]}


@app.post("/api/v1/projects/{project_id}/files")
def upload_project_file(project_id: int, file: UploadFile=File(...), description: str|None=Form(None)):
    with db() as con:
        if not con.execute("SELECT id FROM projects WHERE id=?",(project_id,)).fetchone():
            raise HTTPException(404,"Project not found")
    now=datetime.now(timezone.utc).isoformat()
    folder=Path("/data/project_files");folder.mkdir(parents=True,exist_ok=True)
    original=Path(file.filename or "file").name
    stored=f"{uuid.uuid4().hex}_{original}"
    path=folder/stored
    try:
        with path.open("wb") as out: shutil.copyfileobj(file.file,out)
        size=path.stat().st_size
        if size<=0: raise ValueError("Uploaded file is empty")
        with db() as con:
            cur=con.execute("""INSERT INTO project_files(project_id,file_name,stored_name,content_type,description,created_at)
              VALUES(?,?,?,?,?,?)""",(project_id,original,stored,file.content_type or "application/octet-stream",description,now))
            file_id=cur.lastrowid
        return {"status":"created","id":file_id,"file_name":original,"content_type":file.content_type,"size":size}
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(500,f"Project file upload failed: {exc}") from exc


@app.get("/api/v1/project-files/{file_id}")
def get_project_file(file_id: int):
    with db() as con:
        row=con.execute("SELECT * FROM project_files WHERE id=?",(file_id,)).fetchone()
    if not row: raise HTTPException(404,"File not found")
    path=Path("/data/project_files")/row["stored_name"]
    if not path.exists(): raise HTTPException(404,"Stored project file not found")
    return FileResponse(path,media_type=row["content_type"] or "application/octet-stream",headers={"Content-Disposition":f'inline; filename="{row["file_name"]}"'})


@app.get("/api/v1/my-day")
def my_day():
    tz=ZoneInfo("Europe/Vienna")
    now=datetime.now(tz);start=now.replace(hour=0,minute=0,second=0,microsecond=0);end=start+timedelta(days=1)
    with db() as con:
        tasks=con.execute("""SELECT t.*,c.name customer_name,c.address customer_address,
          c.postal_code customer_postal_code,c.city customer_city,c.country customer_country
          FROM tasks t LEFT JOIN customers c ON c.id=t.customer_id
          WHERE t.status!='completed' AND (t.due_at IS NOT NULL OR t.scheduled_start IS NOT NULL) ORDER BY COALESCE(t.scheduled_start,t.due_at)""").fetchall()
        maint=con.execute("""SELECT m.*,c.name customer_name,c.address customer_address,c.postal_code customer_postal_code,c.city customer_city,c.country customer_country
          FROM maintenance_jobs m JOIN customers c ON c.id=m.customer_id
          WHERE m.status!='completed' AND m.scheduled_at IS NOT NULL ORDER BY m.scheduled_at""").fetchall()
        visits=con.execute("""SELECT v.*,c.name customer_name,c.address customer_address,c.postal_code customer_postal_code,c.city customer_city,c.country customer_country
          FROM service_visits v JOIN customers c ON c.id=v.customer_id
          WHERE v.status!='completed' AND COALESCE(v.scheduled_at,v.visit_date) IS NOT NULL ORDER BY COALESCE(v.scheduled_at,v.visit_date)""").fetchall()
    def due_today_or_overdue(row,key):
        try:
            d=datetime.fromisoformat(row[key]);d=d if d.tzinfo else d.replace(tzinfo=tz)
            return d.astimezone(tz)<end
        except Exception:return False
    return {"date":start.date().isoformat(),"tasks":[dict(x) for x in tasks if due_today_or_overdue(x,"scheduled_start" if x["scheduled_start"] else "due_at")],
      "visits":[dict(x) for x in visits if due_today_or_overdue(x,"scheduled_at" if x["scheduled_at"] else "visit_date")],
      "maintenances":[dict(x) for x in maint if due_today_or_overdue(x,"scheduled_at")]}


@app.get("/api/v1/tasks")
def list_tasks(status: str | None = None):
    with db() as con:
        sql="""SELECT t.*,c.name customer_name,c.address customer_address,c.postal_code customer_postal_code,c.city customer_city,c.country customer_country FROM tasks t
               LEFT JOIN customers c ON c.id=t.customer_id"""
        args=[]
        if status: sql+=" WHERE t.status=?";args.append(status)
        sql+=" ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, COALESCE(t.due_at,'9999')"
        rows=con.execute(sql,args).fetchall()
    return {"tasks":[dict(r) for r in rows]}

@app.post("/api/v1/tasks")
def create_task(item: TaskCreate):
    now=datetime.now(timezone.utc).isoformat()
    with db() as con:
        cur=con.execute("""INSERT INTO tasks
          (title,description,status,priority,due_at,category,project_name,project_id,customer_id,remind_at,scheduled_start,scheduled_end,outlook_event_id,created_at,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(item.title,item.description,item.status,item.priority,
          item.due_at,item.category,item.project_name,item.project_id,item.customer_id,item.remind_at,item.scheduled_start,item.scheduled_end,item.outlook_event_id,now,now))
    return {"status":"created","id":cur.lastrowid}

@app.put("/api/v1/tasks/{task_id}")
def update_task(task_id: int, item: TaskCreate):
    now=datetime.now(timezone.utc).isoformat()
    completed=now if item.status=="completed" else None
    with db() as con:
        cur=con.execute("""UPDATE tasks SET title=?,description=?,status=?,priority=?,due_at=?,
          category=?,project_name=?,project_id=?,customer_id=?,remind_at=?,scheduled_start=?,scheduled_end=?,outlook_event_id=?,reminded_at=CASE WHEN remind_at IS NOT ? THEN NULL ELSE reminded_at END,updated_at=?,completed_at=? WHERE id=?""",
          (item.title,item.description,item.status,item.priority,item.due_at,item.category,
           item.project_name,item.project_id,item.customer_id,item.remind_at,item.scheduled_start,item.scheduled_end,item.outlook_event_id,item.remind_at,now,completed,task_id))
        if cur.rowcount==0: raise HTTPException(404,"Task not found")
    return {"status":"updated","id":task_id}


@app.get("/api/v1/maintenances")
def list_maintenances():
    with db() as con:
        rows=con.execute("""SELECT m.*,c.name customer_name,d.model device_model
          FROM maintenance_jobs m JOIN customers c ON c.id=m.customer_id
          LEFT JOIN devices d ON d.id=m.device_id
          ORDER BY COALESCE(m.scheduled_at,m.created_at)""").fetchall()
    return {"maintenances":[dict(r) for r in rows]}

@app.post("/api/v1/maintenances")
def create_maintenance(item: MaintenanceCreate):
    now=datetime.now(timezone.utc).isoformat()
    with db() as con:
        cur=con.execute("""INSERT INTO maintenance_jobs
          (customer_id,device_id,scheduled_at,status,created_at,updated_at)
          VALUES (?,?,?,'planned',?,?)""",(item.customer_id,item.device_id,item.scheduled_at,now,now))
        mid=cur.lastrowid
        customer=con.execute("SELECT name FROM customers WHERE id=?",(item.customer_id,)).fetchone()
        device=con.execute("SELECT manufacturer,model FROM devices WHERE id=?",(item.device_id,)).fetchone() if item.device_id else None
        device_label=" ".join(x for x in [device["manufacturer"] if device else None,device["model"] if device else None] if x)
        task_title="Wartung"+((" · "+device_label) if device_label else "")
        con.execute("""INSERT INTO tasks
          (title,description,status,priority,due_at,category,customer_id,maintenance_id,created_at,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?)""",
          (task_title,"Geplante Wartung"+((" bei "+customer["name"]) if customer else ""),"open","normal",
           item.scheduled_at,"Wartung",item.customer_id,mid,now,now))
        for order,(section,key,label,unit) in enumerate(MAINTENANCE_TEMPLATE):
            con.execute("""INSERT INTO maintenance_checks
              (maintenance_id,section,item_key,label,unit,sort_order)
              VALUES (?,?,?,?,?,?)""",(mid,section,key,label,unit,order))
    return {"status":"created","id":mid}

class MaintenanceCheckUpdate(BaseModel):
    id: int
    status: str | None = None
    value: str | None = None
    note: str | None = None

class MaintenanceSave(BaseModel):
    status: str = "in_progress"
    interval_months: int = 12
    next_due_date: str | None = None
    burner_runtime: float | None = None
    average_runtime: float | None = None
    software_version: str | None = None
    plant_online: bool | None = None
    system_pressure: float | None = None
    remarks: str | None = None
    material: str | None = None
    checks: list[MaintenanceCheckUpdate] = []

@app.put("/api/v1/maintenances/{maintenance_id}")
def save_maintenance(maintenance_id: int, item: MaintenanceSave):
    completed_at=datetime.now(timezone.utc).isoformat() if item.status=="completed" else None
    next_due=None
    interval_months=12
    if item.status=="completed":
        with db() as con:
            job=con.execute("SELECT device_id FROM maintenance_jobs WHERE id=?",(maintenance_id,)).fetchone()
            if job and job["device_id"]:
                dev=con.execute("SELECT maintenance_interval_months FROM devices WHERE id=?",(job["device_id"],)).fetchone()
                if dev: interval_months=dev["maintenance_interval_months"] or 12
        base=datetime.now(timezone.utc)
        month0=base.month-1+interval_months
        year=base.year+month0//12
        month=month0%12+1
        import calendar
        day=min(base.day,calendar.monthrange(year,month)[1])
        next_due=base.replace(year=year,month=month,day=day).date().isoformat()
    with db() as con:
        cur=con.execute("""UPDATE maintenance_jobs SET status=?,burner_runtime=?,average_runtime=?,
          software_version=?,plant_online=?,system_pressure=?,remarks=?,material=?,completed_at=?,interval_months=?,next_due_date=?,updated_at=?
          WHERE id=?""",(item.status,item.burner_runtime,item.average_runtime,item.software_version,
          None if item.plant_online is None else int(item.plant_online),item.system_pressure,
          item.remarks,item.material,completed_at,interval_months,next_due,datetime.now(timezone.utc).isoformat(),maintenance_id))
        if cur.rowcount==0: raise HTTPException(404,"Maintenance not found")
        if item.status=="completed":
            job=con.execute("SELECT device_id FROM maintenance_jobs WHERE id=?",(maintenance_id,)).fetchone()
            if job and job["device_id"]:
                con.execute("UPDATE devices SET maintenance_next_due_date=?,updated_at=? WHERE id=?",
                    (next_due,datetime.now(timezone.utc).isoformat(),job["device_id"]))
        for x in item.checks:
            con.execute("""UPDATE maintenance_checks SET status=?,value=?,note=?
              WHERE id=? AND maintenance_id=?""",(x.status,x.value,x.note,x.id,maintenance_id))
    return {"status":"updated","id":maintenance_id}


@app.delete("/api/v1/maintenances/{maintenance_id}")
def delete_maintenance(maintenance_id: int):
    with db() as con:
        row=con.execute("SELECT id FROM maintenance_jobs WHERE id=?",(maintenance_id,)).fetchone()
        if row is None:
            raise HTTPException(404,"Maintenance not found")
        con.execute("DELETE FROM tasks WHERE maintenance_id=?",(maintenance_id,))
        con.execute("UPDATE customer_files SET maintenance_id=NULL WHERE maintenance_id=?",(maintenance_id,))
        con.execute("DELETE FROM maintenance_checks WHERE maintenance_id=?",(maintenance_id,))
        con.execute("DELETE FROM maintenance_jobs WHERE id=?",(maintenance_id,))
    return {"status":"deleted","id":maintenance_id}


@app.get("/api/v1/maintenances/{maintenance_id}")
def get_maintenance(maintenance_id: int):
    with db() as con:
        row=con.execute("""SELECT m.*,c.name customer_name,d.model device_model
          FROM maintenance_jobs m JOIN customers c ON c.id=m.customer_id
          LEFT JOIN devices d ON d.id=m.device_id WHERE m.id=?""",(maintenance_id,)).fetchone()
        if row is None: raise HTTPException(404,"Maintenance not found")
        checks=con.execute("SELECT * FROM maintenance_checks WHERE maintenance_id=? ORDER BY sort_order",(maintenance_id,)).fetchall()
    result=dict(row);result["checks"]=[dict(x) for x in checks]
    with db() as con:
        prev=con.execute("""SELECT id,completed_at FROM maintenance_jobs
          WHERE customer_id=? AND id<>? AND status='completed'
          AND (? IS NULL OR device_id=?)
          ORDER BY completed_at DESC LIMIT 1""",
          (row["customer_id"],maintenance_id,row["device_id"],row["device_id"])).fetchone()
        files=con.execute("""SELECT * FROM customer_files WHERE maintenance_id=?
          ORDER BY id DESC""",(maintenance_id,)).fetchall()
        previous_checks=[]
        if prev:
            previous_checks=con.execute("""SELECT item_key,status,value,note FROM maintenance_checks
              WHERE maintenance_id=?""",(prev["id"],)).fetchall()
    result["previous"]={"id":prev["id"],"completed_at":prev["completed_at"],"checks":[dict(x) for x in previous_checks]} if prev else None
    result["files"]=[dict(x) for x in files]
    return result


@app.get("/api/v1/visits")
def list_all_visits(status: str | None = None):
    with db() as con:
        sql="""SELECT v.*,c.name AS customer_name,c.city AS customer_city
               FROM service_visits v JOIN customers c ON c.id=v.customer_id"""
        args=[]
        if status:
            sql+=" WHERE v.status=?";args.append(status)
        sql+=" ORDER BY COALESCE(v.scheduled_at,v.visit_date) ASC,v.id DESC"
        rows=con.execute(sql,args).fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            files=con.execute("""SELECT id,file_name,content_type,description,created_at
                FROM customer_files WHERE visit_id=? ORDER BY id DESC""",(row["id"],)).fetchall()
            item["files"]=[dict(f) for f in files]
            result.append(item)
    return {"visits":result}


@app.get("/api/v1/customers/{customer_id}/visits")
def list_customer_visits(customer_id: int):
    with db() as con:
        rows=con.execute("""SELECT * FROM service_visits WHERE customer_id=?
            ORDER BY visit_date DESC,id DESC""",(customer_id,)).fetchall()
    return {"visits":[dict(r) for r in rows]}


@app.put("/api/v1/customers/{customer_id}/visits/{visit_id}")
def update_customer_visit(customer_id: int, visit_id: int, visit: ServiceVisitCreate):
    with db() as con:
        cur=con.execute("""UPDATE service_visits SET visit_date=?,title=?,description=?,
            duration_hours=?,travel_km=?,material=?,invoice_reference=?,updated_at=?
            WHERE id=? AND customer_id=?""",
            (visit.visit_date,visit.title,visit.description,visit.duration_hours,visit.travel_km,
             visit.material,visit.invoice_reference,datetime.now(timezone.utc).isoformat(),
             visit_id,customer_id))
        if cur.rowcount==0: raise HTTPException(404,"Visit not found")
    return {"status":"updated","id":visit_id}


@app.post("/api/v1/customers/{customer_id}/visits")
def create_customer_visit(customer_id: int, visit: ServiceVisitCreate):
    now=datetime.now(timezone.utc).isoformat()
    with db() as con:
        if con.execute("SELECT 1 FROM customers WHERE id=?",(customer_id,)).fetchone() is None:
            raise HTTPException(404,"Customer not found")
        cur=con.execute("""INSERT INTO service_visits
            (customer_id,visit_date,title,description,duration_hours,travel_km,material,
             invoice_reference,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (customer_id,visit.visit_date,visit.title,visit.description,visit.duration_hours,
             visit.travel_km,visit.material,visit.invoice_reference,now,now))
    return {"status":"created","id":cur.lastrowid}


@app.get("/api/v1/customers/{customer_id}")
def get_customer(customer_id: int):
    with db() as con:
        row = con.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Customer not found")
        installations = con.execute(
            "SELECT * FROM installations WHERE customer_id = ? ORDER BY name COLLATE NOCASE",
            (customer_id,),
        ).fetchall()
    result = dict(row)
    result["installations"] = [dict(r) for r in installations]
    with db() as con:
        devices = con.execute("""SELECT d.* FROM devices d
            JOIN installations i ON i.id=d.installation_id
            WHERE i.customer_id=? ORDER BY d.id""",(customer_id,)).fetchall()
    device_list=[]
    with db() as con:
        for d in devices:
            item=dict(d)
            last=con.execute("""SELECT completed_at FROM maintenance_jobs
                WHERE device_id=? AND status='completed' AND completed_at IS NOT NULL
                ORDER BY completed_at DESC LIMIT 1""",(d["id"],)).fetchone()
            item["maintenance_last_completed_at"]=last["completed_at"] if last else None
            device_list.append(item)
    result["devices"] = device_list
    with db() as con:
        visits=con.execute("""SELECT * FROM service_visits WHERE customer_id=?
            ORDER BY visit_date DESC,id DESC""",(customer_id,)).fetchall()
    result["visits"]=[dict(r) for r in visits]
    with db() as con:
        maintenances=con.execute("""SELECT m.*,d.model device_model,d.manufacturer device_manufacturer
            FROM maintenance_jobs m
            LEFT JOIN devices d ON d.id=m.device_id
            WHERE m.customer_id=?
            ORDER BY COALESCE(m.scheduled_at,m.created_at) DESC,m.id DESC""",(customer_id,)).fetchall()
    result["maintenances"]=[dict(r) for r in maintenances]
    with db() as con:
        files=con.execute("""SELECT * FROM customer_files WHERE customer_id=?
            ORDER BY created_at DESC,id DESC""",(customer_id,)).fetchall()
    result["files"]=[dict(r) for r in files]
    return result


@app.post("/api/v1/customers")
def create_customer(customer: CustomerCreate):
    name = customer.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    now = datetime.now(timezone.utc).isoformat()
    with db() as con:
        cur = con.execute("""
            INSERT INTO customers
            (name,address,postal_code,city,country,phone,mobile,email,notes,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (name,customer.address,customer.postal_code,customer.city,customer.country,
              customer.phone,customer.mobile,customer.email,customer.notes,now,now))
        customer_id = cur.lastrowid
    return {"status":"created","id":customer_id}


@app.get("/api/v1/reminders")
def list_reminders(status: str = "open", _: None = Depends(require_management_key)):
    with db() as con:
        rows = con.execute("""
            SELECT *
            FROM reminders
            WHERE status = ?
            ORDER BY due_at
        """, (status,)).fetchall()

    return {"reminders": [dict(row) for row in rows]}


@app.post("/api/v1/reminders/{reminder_id}/complete")
def complete_reminder(
    reminder_id: int,
    _: None = Depends(require_management_key),
):
    completed_at = datetime.now(timezone.utc).isoformat()

    with db() as con:
        row = con.execute(
            """
            SELECT *
            FROM reminders
            WHERE id = ?
              AND status = 'open'
            """,
            (reminder_id,),
        ).fetchone()

        if row is None:
            raise HTTPException(
                404,
                "Open reminder not found",
            )

        recurrence = row["recurrence"] or "none"
        recurrence_timezone = (
            row["recurrence_timezone"] or "Europe/Vienna"
        )

        if recurrence == "none":
            con.execute(
                """
                UPDATE reminders
                SET status = 'completed',
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    completed_at,
                    reminder_id,
                ),
            )

            return {
                "status": "completed",
                "id": reminder_id,
            }

        next_due = next_recurrence_due(
            row["due_at"],
            recurrence,
            recurrence_timezone,
        )

        con.execute(
            """
            UPDATE reminders
            SET due_at = ?,
                completed_at = NULL,
                notified_at = NULL,
                notification_status = NULL,
                notification_error = NULL,
                status = 'open'
            WHERE id = ?
            """,
            (
                next_due.isoformat(),
                reminder_id,
            ),
        )

    return {
        "status": "rescheduled",
        "id": reminder_id,
        "recurrence": recurrence,
        "next_due_at": next_due.isoformat(),
        "recurrence_timezone": recurrence_timezone,
    }



@app.post("/api/v1/reminders/process")
def process_reminders_now(_: None = Depends(require_management_key)):
    process_due_reminders()
    return {"status": "processed"}


class ReminderReschedule(BaseModel):
    due_at: datetime


@app.get("/api/v1/reminders/today")
def reminders_today(_: None = Depends(require_management_key)):
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59, second=59, microsecond=999999)

    with db() as con:
        rows = con.execute("""
            SELECT *
            FROM reminders
            WHERE status = 'open'
              AND due_at >= ?
              AND due_at <= ?
            ORDER BY due_at
        """, (start.isoformat(), end.isoformat())).fetchall()

    return {"period": "today", "reminders": [dict(row) for row in rows]}


@app.get("/api/v1/reminders/overdue")
def reminders_overdue(_: None = Depends(require_management_key)):
    now = datetime.now(timezone.utc)

    with db() as con:
        rows = con.execute("""
            SELECT *
            FROM reminders
            WHERE status = 'open'
            ORDER BY due_at
        """).fetchall()

    overdue = [
        dict(row)
        for row in rows
        if datetime.fromisoformat(row["due_at"]).astimezone(timezone.utc) < now
    ]

    return {"period": "overdue", "reminders": overdue}


@app.get("/api/v1/reminders/week")
def reminders_week(_: None = Depends(require_management_key)):
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59, second=59, microsecond=999999)

    from datetime import timedelta
    end = end + timedelta(days=6)

    with db() as con:
        rows = con.execute("""
            SELECT *
            FROM reminders
            WHERE status = 'open'
            ORDER BY due_at
        """).fetchall()

    result = []

    for row in rows:
        due = datetime.fromisoformat(row["due_at"]).astimezone()

        if start <= due <= end:
            result.append(dict(row))

    return {"period": "week", "reminders": result}


@app.post("/api/v1/reminders/{reminder_id}/reschedule")
def reschedule_reminder(reminder_id: int, request: ReminderReschedule, _: None = Depends(require_management_key)):
    with db() as con:
        cur = con.execute("""
            UPDATE reminders
            SET due_at = ?,
                notified_at = NULL,
                notification_status = NULL,
                notification_error = NULL
            WHERE id = ?
              AND status = 'open'
        """, (
            request.due_at.isoformat(),
            reminder_id,
        ))

        if cur.rowcount == 0:
            raise HTTPException(404, "Open reminder not found")

    return {
        "status": "rescheduled",
        "id": reminder_id,
        "due_at": request.due_at,
    }


# MCP endpoint
app.mount("/mcp", mcp_http_app)
