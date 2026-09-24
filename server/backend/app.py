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

OEKOFEN_TOKEN_URL = "https://my.oekofen.info/api/pwa/v1/oauth2/token"
OEKOFEN_PLANTS_URL = "https://my.oekofen.info/api/pwa/v3/plants"

app = FastAPI(
    title="INS-EI API",
    description="Backend API for INS Energy Intelligence",
    version=VERSION,
)

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

        if not column_exists(con, "devices", "oekofen_plant_id"):
            con.execute("ALTER TABLE devices ADD COLUMN oekofen_plant_id TEXT")
        if not column_exists(con, "devices", "ins_installation_id"):
            con.execute("ALTER TABLE devices ADD COLUMN ins_installation_id TEXT")

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
                SELECT push_enabled, first_seen_at
                FROM oekofen_plants
                WHERE plant_id = ?
                """,
                (plant_id,),
            ).fetchone()

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


def reminder_worker():
    while True:
        try:
            process_due_reminders()
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
             construction_year,commissioning_date,power_kw,online_capable,notes,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (installation_id,"heating",device.manufacturer,device.model,device.serial_number,
             device.touch_id,device.construction_year,device.commissioning_date,device.power_kw,
             1 if device.manufacturer.lower() in ("ökofen","oekofen") else 0,
             device.notes,now,now))
        device_id=cur.lastrowid
    return {"status":"created","id":device_id,"installation_id":installation_id}


class DeviceLinks(BaseModel):
    oekofen_plant_id: str | None = None
    ins_installation_id: str | None = None


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
            (customer_id,visit_id,maintenance_id,file_name,stored_name,content_type,description,created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (customer_id,visit_id,maintenance_id,file.filename or stored_name,stored_name,
             file.content_type,description,now))
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
    return FileResponse(path,media_type=row["content_type"],filename=row["file_name"])


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
    result["devices"] = [dict(r) for r in devices]
    with db() as con:
        visits=con.execute("""SELECT * FROM service_visits WHERE customer_id=?
            ORDER BY visit_date DESC,id DESC""",(customer_id,)).fetchall()
    result["visits"]=[dict(r) for r in visits]
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
