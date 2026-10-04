from datetime import datetime, timezone, timedelta, date
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
import hashlib
import hmac
import secrets
import paho.mqtt.client as mqtt

from pywebpush import webpush, WebPushException
from cryptography.fernet import Fernet, InvalidToken

from fastapi import FastAPI, HTTPException, Header, Depends, UploadFile, File, Form, Cookie, Response, Request as FastAPIRequest
from fastapi.responses import FileResponse, JSONResponse
from webauthn import (
    generate_registration_options, verify_registration_response,
    generate_authentication_options, verify_authentication_response,
    options_to_json,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, ResidentKeyRequirement,
    UserVerificationRequirement, PublicKeyCredentialDescriptor,
)
from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

VERSION = "1.7.4"
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
MQTT_USERNAME = Path("/run/secrets/mqtt_username")
MQTT_PASSWORD = Path("/run/secrets/mqtt_password")
SEVDESK_API_TOKEN = Path("/run/secrets/sevdesk_api_token")
FINANCE_TAX_RESERVE_EUR = Path("/run/secrets/finance_tax_reserve_eur")
FINANCE_OPERATING_BUFFER_EUR = Path("/run/secrets/finance_operating_buffer_eur")
CREDENTIAL_VAULT_KEY = Path("/data/credential_vault.key")
SEVDESK_API_BASE = "https://my.sevdesk.de/api/v1"
MQTT_HOST = "mqtt.ins-enertech.net"
MQTT_PORT = 8883
MQTT_LIVE = {}
MQTT_LIVE_LOCK = threading.Lock()
MQTT_SERVICE_CLIENT = None
MQTT_SERVICE_LOCK = threading.Lock()

OEKOFEN_TOKEN_URL = "https://my.oekofen.info/api/pwa/v1/oauth2/token"
OEKOFEN_PLANTS_URL = "https://my.oekofen.info/api/pwa/v3/plants"

def _store_bus_snapshot(site_id: str, kind: str, envelope: dict[str, Any]) -> None:
    if envelope.get("api_version") != "ins-ei.bus/v1":
        raise ValueError("BUS_API_VERSION_UNSUPPORTED")
    if envelope.get("site_id") != site_id:
        raise ValueError("BUS_SITE_ID_MISMATCH")
    generated_at = str(envelope.get("generated_at") or "")
    if not generated_at:
        raise ValueError("BUS_GENERATED_AT_MISSING")
    received_at = datetime.now(timezone.utc).isoformat()
    with db() as con:
        con.execute("""
            INSERT INTO bus_site_snapshots
            (site_id,kind,api_version,generated_at,received_at,sequence,core_version,payload_json)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(site_id,kind) DO UPDATE SET
                api_version=excluded.api_version,
                generated_at=excluded.generated_at,
                received_at=excluded.received_at,
                sequence=excluded.sequence,
                core_version=excluded.core_version,
                payload_json=excluded.payload_json
        """, (
            site_id, kind, envelope["api_version"], generated_at, received_at,
            envelope.get("sequence"), envelope.get("core_version"),
            json.dumps(envelope.get("payload") or {}, ensure_ascii=False, default=str),
        ))


def mqtt_live_worker():
    if not MQTT_USERNAME.exists() or not MQTT_PASSWORD.exists():
        print("MQTT live disabled: credentials missing");return
    username=MQTT_USERNAME.read_text().strip();password=MQTT_PASSWORD.read_text().strip()
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id="ins-ei-service-bus-v1")
    client.username_pw_set(username,password);client.tls_set()
    def on_connect(client,userdata,flags,reason_code,properties):
        global MQTT_SERVICE_CLIENT
        with MQTT_SERVICE_LOCK:
            MQTT_SERVICE_CLIENT = client
        print("MQTT bus connected:",reason_code)
        if reason_code==0:
            for topic,qos in (
                ("ins-ei/+/status",1),("ins-ei/+/health",1),("ins-ei/+/learning",1),
                ("ins-ei/+/state",0),("ins-ei/+/event",1),("ins-ei/+/decision",1),
                ("ins-ei/+/command/result",1),("ins-ei/+/update/status",1),
            ):
                client.subscribe(topic,qos=qos)
    def on_message(client,userdata,msg):
        try:
            parts=msg.topic.split("/")
            if len(parts)<3:return
            site_id=parts[1];kind="/".join(parts[2:])
            payload=json.loads(msg.payload.decode("utf-8"))
            now=datetime.now(timezone.utc).isoformat()

            # New canonical Bus v1 envelope.
            if payload.get("api_version")=="ins-ei.bus/v1":
                if kind in {"status","health","learning","state","update/status"}:
                    _store_bus_snapshot(site_id,kind,payload)
                else:
                    with db() as con:
                        con.execute("""INSERT INTO bus_site_events
                            (site_id,kind,generated_at,received_at,correlation_id,payload_json)
                            VALUES (?,?,?,?,?,?)""",(
                            site_id,kind,str(payload.get("generated_at") or now),now,
                            payload.get("correlation_id"),
                            json.dumps(payload.get("payload") or {},ensure_ascii=False,default=str),
                        ))
                # Keep the existing live endpoint useful during migration.
                with MQTT_LIVE_LOCK:
                    item=MQTT_LIVE.setdefault(site_id,{"values":{},"online":False})
                    if kind=="status":
                        body=payload.get("payload") or {}
                        item["online"]=bool(body.get("online"))
                        item["version"]=payload.get("core_version")
                        item["status_received_at"]=now
                    elif kind=="state":
                        item["values"]=(payload.get("payload") or {}).get("values") or {}
                        item["source_timestamp"]=payload.get("generated_at");item["received_at"]=now
                return

            # Legacy pilot payload compatibility; no new development targets this format.
            if len(parts)==3:
                installation_id,legacy_kind=parts[1],parts[2]
                with MQTT_LIVE_LOCK:
                    item=MQTT_LIVE.setdefault(installation_id,{"values":{},"online":False})
                    if legacy_kind=="state":
                        item["values"]=payload.get("values") if isinstance(payload.get("values"), dict) else payload
                        item["source_timestamp"]=payload.get("ts");item["received_at"]=now
                    elif legacy_kind=="status":
                        item["online"]=payload.get("status")=="online";item["version"]=payload.get("version");item["status_received_at"]=now
        except Exception as exc: print("MQTT bus message error:",repr(exc))
    client.on_connect=on_connect;client.on_message=on_message
    while True:
        try:
            client.connect(MQTT_HOST,MQTT_PORT,keepalive=30);client.loop_forever(retry_first_connection=True)
        except Exception as exc:
            print("MQTT bus connection error:",repr(exc));time.sleep(5)


CUSTOMER_PORTAL_DIR = Path("/app/customer-portal")
CUSTOMER_PORTAL_DEV_DIR = Path("/app/customer-portal-dev")

app = FastAPI(
    title="INS-EI API",
    description="Backend API for INS Energy Intelligence",
    version=VERSION,
)


SERVICE_AUTH_RP_ID = "ins-ei.ins-enertech.net"
SERVICE_AUTH_ORIGIN = "https://ins-ei.ins-enertech.net"
SERVICE_AUTH_COOKIE = "ins_service_session"
SERVICE_AUTH_DAYS = 30

def init_service_auth_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS service_passkeys (
            credential_id TEXT PRIMARY KEY,
            public_key BLOB NOT NULL,
            sign_count INTEGER NOT NULL DEFAULT 0,
            device_name TEXT,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS service_auth_challenges (
            token TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            challenge BLOB NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS service_auth_sessions (
            token TEXT PRIMARY KEY,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        """)

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _service_auth_configured() -> bool:
    with db() as con:
        return bool(con.execute("SELECT 1 FROM service_passkeys LIMIT 1").fetchone())

def _service_session_valid(token: str | None) -> bool:
    if not token:
        return False
    now = datetime.now(timezone.utc)
    with db() as con:
        row=con.execute("SELECT expires_at FROM service_auth_sessions WHERE token=?",(token,)).fetchone()
        if row is None or row["expires_at"] <= now.isoformat():
            if row is not None: con.execute("DELETE FROM service_auth_sessions WHERE token=?",(token,))
            return False
        con.execute("UPDATE service_auth_sessions SET last_seen_at=? WHERE token=?",(now.isoformat(),token))
    return True

def _new_service_session(response: Response) -> None:
    token=secrets.token_urlsafe(32);now=datetime.now(timezone.utc)
    expires=now+timedelta(days=SERVICE_AUTH_DAYS)
    with db() as con:
        con.execute("INSERT INTO service_auth_sessions(token,expires_at,created_at,last_seen_at) VALUES(?,?,?,?)",
                    (token,expires.isoformat(),now.isoformat(),now.isoformat()))
    response.set_cookie(SERVICE_AUTH_COOKIE,token,max_age=SERVICE_AUTH_DAYS*86400,
                        httponly=True,secure=True,samesite="lax",path="/")

def _challenge(kind: str, challenge: bytes) -> str:
    token=secrets.token_urlsafe(24)
    with db() as con:
        con.execute("DELETE FROM service_auth_challenges WHERE expires_at<=?",(datetime.now(timezone.utc).isoformat(),))
        con.execute("INSERT INTO service_auth_challenges(token,kind,challenge,expires_at) VALUES(?,?,?,?)",
                    (token,kind,challenge,(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()))
    return token

def _take_challenge(token: str, kind: str) -> bytes:
    with db() as con:
        row=con.execute("SELECT challenge,expires_at FROM service_auth_challenges WHERE token=? AND kind=?",(token,kind)).fetchone()
        con.execute("DELETE FROM service_auth_challenges WHERE token=?",(token,))
    if row is None or row["expires_at"] <= datetime.now(timezone.utc).isoformat():
        raise HTTPException(400,"AUTH_CHALLENGE_INVALID")
    return bytes(row["challenge"])

SERVICE_AUTH_PUBLIC_EXACT={"/health"}

def _service_machine_endpoint(method: str, path: str) -> bool:
    if path.startswith("/api/v1/auth/"):
        return True
    if method=="POST" and path=="/api/v1/telemetry":
        return True
    if path.startswith("/api/v1/fleet/"):
        tail=path.split("/api/v1/fleet/",1)[1].split("/")
        # Agent-only endpoints: heartbeat, polling for commands, and command result callback.
        if len(tail)>=3 and tail[1:3]==["update-agent","heartbeat"] and method=="POST": return True
        if len(tail)>=3 and tail[1:3]==["update-agent","command"] and method=="GET": return True
        if len(tail)>=4 and tail[1]=="command" and tail[3]=="result" and method=="POST": return True
    return False

@app.middleware("http")
async def service_auth_guard(request: FastAPIRequest, call_next):
    path=request.url.path
    if path.startswith("/portal") or path.startswith("/mcp") or path in SERVICE_AUTH_PUBLIC_EXACT:
        return await call_next(request)
    if not path.startswith("/api/"):
        return await call_next(request)
    if _service_machine_endpoint(request.method,path):
        return await call_next(request)
    # Existing management-key integrations remain independent from browser sessions.
    if request.headers.get("x-api-key") or request.headers.get("x-ins-ei-key"):
        return await call_next(request)
    if not _service_auth_configured():
        return await call_next(request)
    if not _service_session_valid(request.cookies.get(SERVICE_AUTH_COOKIE)):
        return JSONResponse({"detail":"SERVICE_LOGIN_REQUIRED"},status_code=401)
    return await call_next(request)

@app.get("/api/v1/auth/status")
def service_auth_status(ins_service_session: str | None = Cookie(default=None)):
    configured=_service_auth_configured()
    return {"configured":configured,"authenticated":_service_session_valid(ins_service_session) if configured else False}

@app.post("/api/v1/auth/register/options")
def service_register_options(request: FastAPIRequest, ins_service_session: str | None = Cookie(default=None)):
    if _service_auth_configured() and not _service_session_valid(ins_service_session):
        raise HTTPException(401,"SERVICE_LOGIN_REQUIRED")
    options=generate_registration_options(
        rp_id=SERVICE_AUTH_RP_ID,
        rp_name="INS-EI Servicezentrale",
        user_id=b"niki-admin",
        user_name="niki",
        user_display_name="Niki",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    token=_challenge("register",options.challenge)
    return {"token":token,"options":json.loads(options_to_json(options))}

@app.post("/api/v1/auth/register/verify")
async def service_register_verify(request: FastAPIRequest, response: Response):
    body=await request.json();token=str(body.pop("token",""))
    challenge=_take_challenge(token,"register")
    try:
        verification=verify_registration_response(
            credential=body,
            expected_challenge=challenge,
            expected_rp_id=SERVICE_AUTH_RP_ID,
            expected_origin=SERVICE_AUTH_ORIGIN,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(400,f"PASSKEY_REGISTRATION_FAILED: {exc}")
    cid=_b64url(verification.credential_id)
    with db() as con:
        con.execute("""INSERT OR REPLACE INTO service_passkeys
            (credential_id,public_key,sign_count,device_name,created_at,last_used_at)
            VALUES(?,?,?,?,?,?)""",(cid,verification.credential_public_key,verification.sign_count,
                request.headers.get("user-agent","")[:160],datetime.now(timezone.utc).isoformat(),None))
    _new_service_session(response)
    return {"registered":True}

@app.post("/api/v1/auth/login/options")
def service_login_options():
    if not _service_auth_configured():
        raise HTTPException(409,"PASSKEY_NOT_CONFIGURED")
    with db() as con:
        rows=con.execute("SELECT credential_id FROM service_passkeys").fetchall()
    descriptors=[PublicKeyCredentialDescriptor(id=base64.urlsafe_b64decode(r["credential_id"]+"="*((4-len(r["credential_id"])%4)%4))) for r in rows]
    options=generate_authentication_options(
        rp_id=SERVICE_AUTH_RP_ID,
        allow_credentials=descriptors,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    token=_challenge("login",options.challenge)
    return {"token":token,"options":json.loads(options_to_json(options))}

@app.post("/api/v1/auth/login/verify")
async def service_login_verify(request: FastAPIRequest, response: Response):
    body=await request.json();token=str(body.pop("token",""))
    challenge=_take_challenge(token,"login")
    cid=str(body.get("id") or "")
    with db() as con:
        row=con.execute("SELECT * FROM service_passkeys WHERE credential_id=?",(cid,)).fetchone()
    if row is None: raise HTTPException(401,"PASSKEY_UNKNOWN")
    try:
        verification=verify_authentication_response(
            credential=body,
            expected_challenge=challenge,
            expected_rp_id=SERVICE_AUTH_RP_ID,
            expected_origin=SERVICE_AUTH_ORIGIN,
            credential_public_key=bytes(row["public_key"]),
            credential_current_sign_count=int(row["sign_count"]),
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(401,f"PASSKEY_LOGIN_FAILED: {exc}")
    with db() as con:
        con.execute("UPDATE service_passkeys SET sign_count=?,last_used_at=? WHERE credential_id=?",
                    (verification.new_sign_count,datetime.now(timezone.utc).isoformat(),cid))
    _new_service_session(response)
    return {"authenticated":True}

@app.post("/api/v1/auth/logout")
def service_logout(response: Response, ins_service_session: str | None = Cookie(default=None)):
    if ins_service_session:
        with db() as con: con.execute("DELETE FROM service_auth_sessions WHERE token=?",(ins_service_session,))
    response.delete_cookie(SERVICE_AUTH_COOKIE,path="/")
    return {"logged_out":True}


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


def _secret_float(path: Path, default: float = 0.0) -> float:
    if not path.exists():
        return default
    try:
        return float(path.read_text().strip().replace(",", "."))
    except (ValueError, OSError):
        return default


def _sevdesk_get(resource: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Read one sevdesk collection. This integration is intentionally GET-only."""
    if not SEVDESK_API_TOKEN.exists():
        raise RuntimeError("SEVDESK_API_TOKEN_MISSING")
    token = SEVDESK_API_TOKEN.read_text().strip()
    if not token:
        raise RuntimeError("SEVDESK_API_TOKEN_EMPTY")
    url = f"{SEVDESK_API_BASE}/{resource}"
    if params:
        url += "?" + urlencode(params)
    request = Request(
        url,
        headers={
            "Authorization": token,
            "Accept": "application/json",
            "User-Agent": f"INS-EI/{VERSION}",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"SEVDESK_HTTP_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"SEVDESK_REQUEST_FAILED: {exc}") from exc
    objects = payload.get("objects", [])
    return objects if isinstance(objects, list) else [objects]


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _sevdesk_finance_check() -> dict[str, Any]:
    accounts = _sevdesk_get("CheckAccount")
    active_accounts = [
        a for a in accounts
        if str(a.get("status", "100")) == "100"
        and str(a.get("currency", "EUR")).upper() == "EUR"
    ]
    liquidity = round(sum(_money(a.get("balance")) for a in active_accounts), 2)

    open_invoices = []
    for status in (200, 750):
        open_invoices.extend(_sevdesk_get("Invoice", {"status": status}))
    receivables = round(sum(_money(i.get("sumGross")) for i in open_invoices), 2)

    # sevdesk voucher states differ from invoice states. Query the collection once
    # and treat every non-draft, non-paid expense voucher as an outstanding payable.
    vouchers = _sevdesk_get("Voucher")
    open_vouchers = [
        v for v in vouchers
        if str(v.get("voucherType", "")).upper() in {"VOU", "RE", "AUSGABE", ""}
        and str(v.get("status", "")) not in {"50", "100", "1000"}
    ]
    payables = round(sum(_money(v.get("sumGross")) for v in open_vouchers), 2)

    tax_reserve = max(0.0, _secret_float(FINANCE_TAX_RESERVE_EUR, 0.0))
    operating_buffer = max(0.0, _secret_float(FINANCE_OPERATING_BUFFER_EUR, 0.0))
    withdrawable = round(max(0.0, liquidity - payables - tax_reserve - operating_buffer), 2)

    configured = FINANCE_TAX_RESERVE_EUR.exists() and FINANCE_OPERATING_BUFFER_EUR.exists()
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "currency": "EUR",
        "liquidity": liquidity,
        "open_receivables": receivables,
        "open_payables": payables,
        "tax_reserve": round(tax_reserve, 2),
        "operating_buffer": round(operating_buffer, 2),
        "withdrawable_now": withdrawable if configured else None,
        "recommendation_ready": configured,
        "warning": None if configured else "FINANCE_RESERVES_NOT_CONFIGURED",
        "accounts": [
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "type": a.get("type"),
                "balance": _money(a.get("balance")),
                "last_sync": a.get("lastSync"),
            }
            for a in active_accounts
        ],
        "counts": {
            "accounts": len(active_accounts),
            "open_invoices": len(open_invoices),
            "open_vouchers": len(open_vouchers),
        },
        "formula": "liquidity - open_payables - tax_reserve - operating_buffer",
        "read_only": True,
    }


def _date_value(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)[:10]
    return ""


def _sevdesk_month_summary(year: int, month: int) -> dict[str, Any]:
    if month < 1 or month > 12:
        raise ValueError("month must be between 1 and 12")
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])

    invoices = _sevdesk_get("Invoice")
    month_invoices = [
        i for i in invoices
        if start.isoformat() <= _date_value(i, "invoiceDate", "create") <= end.isoformat()
        and str(i.get("status", "")) not in {"50", "100"}
    ]
    revenue_gross = round(sum(_money(i.get("sumGross")) for i in month_invoices), 2)
    revenue_net = round(sum(_money(i.get("sumNet")) for i in month_invoices), 2)

    vouchers = _sevdesk_get("Voucher")
    month_vouchers = [
        v for v in vouchers
        if start.isoformat() <= _date_value(v, "voucherDate", "deliveryDate", "create") <= end.isoformat()
        and str(v.get("status", "")) not in {"50"}
    ]
    expenses_gross = round(sum(_money(v.get("sumGross")) for v in month_vouchers), 2)
    expenses_net = round(sum(_money(v.get("sumNet")) for v in month_vouchers), 2)

    operating_surplus_net = round(revenue_net - expenses_net, 2)
    return {
        "period": f"{year:04d}-{month:02d}",
        "revenue_gross": revenue_gross,
        "revenue_net": revenue_net,
        "expenses_gross": expenses_gross,
        "expenses_net": expenses_net,
        "operating_surplus_net_before_tax_svs": operating_surplus_net,
        "counts": {
            "invoices": len(month_invoices),
            "vouchers": len(month_vouchers),
        },
        "read_only": True,
        "note": "Accounting month summary from sevdesk invoices and vouchers; before income tax and SVS.",
    }


@mcp.tool()
def ins_ei_finance_month(year: int, month: int) -> dict[str, Any]:
    """Return sevdesk revenue, expenses and operating surplus for one month."""
    return _sevdesk_month_summary(year, month)


@app.get("/api/v1/finance/month/{year}/{month}")
def finance_month(year: int, month: int, _: bool = Depends(require_management_api_key)):
    try:
        return _sevdesk_month_summary(year, month)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc


@mcp.tool()
def ins_ei_finance_check() -> dict[str, Any]:
    """Return the current read-only sevdesk finance check and possible private withdrawal."""
    return _sevdesk_finance_check()


@app.get("/api/v1/finance/check")
def finance_check(_: bool = Depends(require_management_api_key)):
    """Read-only finance check based on sevdesk data and configured reserves."""
    try:
        return _sevdesk_finance_check()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


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


def bus_snapshot(site_id: str, kind: str) -> dict[str, Any]:
    with db() as con:
        row=con.execute("""SELECT * FROM bus_site_snapshots
            WHERE site_id=? AND kind=?""",(site_id,kind)).fetchone()
    if row is None:
        raise ValueError(f"BUS_SNAPSHOT_NOT_FOUND:{site_id}:{kind}")
    return {
        "site_id":row["site_id"],"kind":row["kind"],"api_version":row["api_version"],
        "generated_at":row["generated_at"],"received_at":row["received_at"],
        "sequence":row["sequence"],"core_version":row["core_version"],
        "payload":json.loads(row["payload_json"] or "{}"),
    }


@mcp.tool()
def ins_ei_learning_summary(site_id: str) -> dict[str, Any]:
    """Return the latest Learning Summary received from one INS-EI Core site via MQTT."""
    return bus_snapshot(site_id,"learning")


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



def init_portal_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS portal_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS portal_dashboards (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            module TEXT NOT NULL,
            config_json TEXT NOT NULL DEFAULT '{}',
            sort_order INTEGER NOT NULL DEFAULT 100
        );
        CREATE TABLE IF NOT EXISTS portal_user_dashboards (
            user_id INTEGER NOT NULL,
            dashboard_id TEXT NOT NULL,
            PRIMARY KEY(user_id,dashboard_id),
            FOREIGN KEY(user_id) REFERENCES portal_users(id),
            FOREIGN KEY(dashboard_id) REFERENCES portal_dashboards(id)
        );
        CREATE TABLE IF NOT EXISTS portal_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES portal_users(id)
        );

        CREATE TABLE IF NOT EXISTS portal_dashboard_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            device_id INTEGER,
            plant_id TEXT,
            name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            config_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS portal_dashboard_access (
            dashboard_config_id INTEGER NOT NULL, user_id INTEGER NOT NULL, permission TEXT NOT NULL DEFAULT 'read', PRIMARY KEY(dashboard_config_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS portal_dashboard_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dashboard_config_id INTEGER NOT NULL, source_type TEXT NOT NULL, name TEXT NOT NULL, source_ref TEXT NOT NULL, config_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS portal_dashboard_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            block_type TEXT NOT NULL,
            source_type TEXT,
            template_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)
        for col,ddl in [
            ("role","TEXT NOT NULL DEFAULT 'end_customer'"),
            ("access_status","TEXT NOT NULL DEFAULT 'active'"),
            ("trial_start","TEXT"),
            ("trial_end","TEXT"),
            ("custom_dashboard_id","INTEGER")
        ]:
            try: con.execute(f"ALTER TABLE portal_users ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError: pass
        con.execute("UPDATE portal_users SET role='admin' WHERE is_admin=1")
        con.execute("""INSERT OR IGNORE INTO portal_dashboards
            (id,name,description,module,config_json,sort_order) VALUES(?,?,?,?,?,?)""",
            ("oschmalz-heating","Temporäre Heizung",
             "Wohnzimmer, Küche, Schlafzimmer und Bad","heating",
             json.dumps({"rooms":["Wohnzimmer","Küche","Schlafzimmer","Bad"]}),10))


def _ensure_portal_admin():
    """Bootstrap only the first administrator from a server secret."""
    admin_password = Path("/run/secrets/portal_admin_password")
    if not admin_password.exists() or not admin_password.read_text().strip():
        return
    with db() as con:
        existing = con.execute("SELECT id FROM portal_users WHERE username='niki'").fetchone()
        if existing:
            user_id = existing["id"]
        else:
            salt = secrets.token_hex(16)
            password_hash = _portal_hash(admin_password.read_text().strip(), salt)
            cur = con.execute("""INSERT INTO portal_users
                (username,display_name,password_salt,password_hash,is_admin,created_at)
                VALUES(?,?,?,?,1,?)""",
                ("niki","Niki",salt,password_hash,datetime.now(timezone.utc).isoformat()))
            user_id = cur.lastrowid
        for dashboard in con.execute("SELECT id FROM portal_dashboards"):
            con.execute("INSERT OR IGNORE INTO portal_user_dashboards(user_id,dashboard_id) VALUES(?,?)",(user_id,dashboard["id"]))



def credential_cipher() -> Fernet:
    if not CREDENTIAL_VAULT_KEY.exists():
        CREDENTIAL_VAULT_KEY.write_bytes(Fernet.generate_key())
        try: os.chmod(CREDENTIAL_VAULT_KEY, 0o600)
        except OSError: pass
    return Fernet(CREDENTIAL_VAULT_KEY.read_bytes().strip())

def encrypt_credential(value: str | None) -> str | None:
    return credential_cipher().encrypt(value.encode()).decode() if value else None

def decrypt_credential(value: str | None) -> str | None:
    if not value: return None
    try: return credential_cipher().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError): raise HTTPException(500, "Credential vault decryption failed")

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
        if not column_exists(con, "devices", "maintenance_customer"):
            con.execute("ALTER TABLE devices ADD COLUMN maintenance_customer INTEGER NOT NULL DEFAULT 0")

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

        con.execute("""
            CREATE TABLE IF NOT EXISTS customer_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL, device_id INTEGER,
                credential_type TEXT NOT NULL DEFAULT 'Sonstiges', title TEXT NOT NULL, url TEXT, username TEXT,
                secret_encrypted TEXT, notes_encrypted TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id), FOREIGN KEY (device_id) REFERENCES devices(id)
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_customer_credentials_customer ON customer_credentials (customer_id)")

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
        if not column_exists(con, "service_visits", "device_id"):
            con.execute("ALTER TABLE service_visits ADD COLUMN device_id INTEGER")
        if not column_exists(con, "service_visits", "category"):
            con.execute("ALTER TABLE service_visits ADD COLUMN category TEXT NOT NULL DEFAULT 'Notiz'")
        if not column_exists(con, "service_visits", "entry_type"):
            con.execute("ALTER TABLE service_visits ADD COLUMN entry_type TEXT NOT NULL DEFAULT 'note'")

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


        con.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_health_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                installation_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                point TEXT NOT NULL,
                quality TEXT NOT NULL,
                source TEXT,
                value TEXT,
                unit TEXT
            )
        """)
        con.execute("""CREATE INDEX IF NOT EXISTS idx_telemetry_health_issues_installation_time
            ON telemetry_health_issues (installation_id,timestamp)""")

        con.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_health_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                installation_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                good INTEGER NOT NULL DEFAULT 0,
                stale INTEGER NOT NULL DEFAULT 0,
                unavailable INTEGER NOT NULL DEFAULT 0
            )
        """)
        con.execute("""CREATE INDEX IF NOT EXISTS idx_telemetry_health_installation_time
            ON telemetry_health_samples (installation_id,timestamp)""")

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


        if not column_exists(con, "telemetry_installations", "latitude"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN latitude REAL")
        if not column_exists(con, "telemetry_installations", "longitude"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN longitude REAL")
        if not column_exists(con, "telemetry_installations", "elevation"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN elevation REAL")
        if not column_exists(con, "telemetry_installations", "time_zone"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN time_zone TEXT")
        if not column_exists(con, "telemetry_installations", "location_name"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN location_name TEXT")


        if not column_exists(con, "telemetry_installations", "health_json"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN health_json TEXT")
        if not column_exists(con, "telemetry_installations", "addon_version"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN addon_version TEXT")
        if not column_exists(con, "telemetry_installations", "health_updated_at"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN health_updated_at TEXT")


        if not column_exists(con, "telemetry_installations", "fleet_status"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN fleet_status TEXT")
        if not column_exists(con, "telemetry_installations", "fleet_status_since"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN fleet_status_since TEXT")
        if not column_exists(con, "telemetry_installations", "fleet_notified_status"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN fleet_notified_status TEXT")
        if not column_exists(con, "telemetry_installations", "fleet_push_enabled"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN fleet_push_enabled INTEGER NOT NULL DEFAULT 1")
        if not column_exists(con, "telemetry_installations", "previous_addon_version"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN previous_addon_version TEXT")
        if not column_exists(con, "telemetry_installations", "version_changed_at"):
            con.execute("ALTER TABLE telemetry_installations ADD COLUMN version_changed_at TEXT")


        con.execute("""
            CREATE TABLE IF NOT EXISTS bus_site_snapshots (
                site_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                api_version TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                sequence INTEGER,
                core_version TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (site_id, kind)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS bus_site_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                correlation_id TEXT,
                payload_json TEXT NOT NULL
            )
        """)
        con.execute("""CREATE INDEX IF NOT EXISTS idx_bus_site_events_site_time
            ON bus_site_events(site_id, generated_at)""")

        con.execute("""
            CREATE TABLE IF NOT EXISTS fleet_update_agents (
                installation_id TEXT PRIMARY KEY,
                agent_version TEXT,
                last_seen_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ONLINE'
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS fleet_update_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                installation_id TEXT NOT NULL,
                target_version TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL,
                claimed_at TEXT,
                completed_at TEXT,
                error TEXT
            )
        """)


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

        if not column_exists(con, "oekofen_plants", "analysis_enabled"):
            con.execute("ALTER TABLE oekofen_plants ADD COLUMN analysis_enabled INTEGER NOT NULL DEFAULT 0")
        if not column_exists(con, "oekofen_plants", "monitoring_enabled"):
            con.execute("ALTER TABLE oekofen_plants ADD COLUMN monitoring_enabled INTEGER NOT NULL DEFAULT 1")
        if not column_exists(con, "oekofen_plants", "visible_in_overview"):
            con.execute("ALTER TABLE oekofen_plants ADD COLUMN visible_in_overview INTEGER NOT NULL DEFAULT 1")

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

        con.execute("""
            CREATE TABLE IF NOT EXISTS oekofen_csv_imports (
                plant_id TEXT NOT NULL,
                day TEXT NOT NULL,
                status TEXT NOT NULL,
                rows_imported INTEGER NOT NULL DEFAULT 0,
                columns_found INTEGER NOT NULL DEFAULT 0,
                bytes_downloaded INTEGER NOT NULL DEFAULT 0,
                encoding TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                error TEXT,
                PRIMARY KEY (plant_id, day)
            )
        """)



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
                SELECT push_enabled, first_seen_at, problem_count, monitoring_enabled
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

            monitoring_enabled = int(existing["monitoring_enabled"]) if existing and "monitoring_enabled" in existing.keys() else 1
            if is_new_problem and push_enabled and monitoring_enabled:
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


def oekofen_fetch_variables(plant_id: str, names: list[str]) -> list[dict[str, Any]]:
    auth = oekofen_login()
    def fetch(token: str):
        url = "https://my.oekofen.info/api/pwa/v1/plants/" + plant_id + "/remotecontrol/variables"
        request = Request(url, data=json.dumps(names).encode("utf-8"), method="POST", headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Accept-Language": "de",
            "Content-Type": "text/plain",
            "X-App-Version": "3.28.4+393",
        })
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("status") != "success":
            raise RuntimeError(str(result.get("message") or "OekoFEN variables request failed"))
        return result.get("data") or []
    try:
        return fetch(auth["access_token"])
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        return fetch(oekofen_login()["access_token"])

def oekofen_format_variable(item):
    if not item or item.get("status") != "OK":
        return None
    raw = str(item.get("value") or "")
    formats = str(item.get("formatTexts") or "")
    if formats:
        try:
            idx = int(float(raw)); parts = formats.split("|")
            if 0 <= idx < len(parts):
                return parts[idx]
        except Exception:
            pass
    try:
        value = float(raw) / float(item.get("divisor") or 1)
        text = (("%.2f" % value).rstrip("0").rstrip(".")).replace(".", ",")
    except Exception:
        text = raw
    unit = str(item.get("unitText") or "").strip()
    return text + ((" " + unit) if unit else "")

OEKOFEN_MEASUREMENTS = [
 ("Bedienteil","CAPPL:LOCAL.touch[0].version",None),
 ("Außentemperatur","CAPPL:LOCAL.L_aussentemperatur_ist",None),
 ("Bestehender Kessel","CAPPL:LOCAL.L_bestke_temp_ist",None),
 ("Kesseltemperatur","CAPPL:LOCAL.L_ke_temp_ist","CAPPL:LOCAL.L_ke_temp_soll"),
 ("Brenneranforderung","CAPPL:LOCAL.L_ke_brennerkontakt_1",None),
 ("Umschaltventil","CAPPL:LOCAL.L_bestke_umschaltventil",None),
 ("PU1 Oben TPO","CAPPL:LOCAL.L_pu[0].einschaltfuehler_ist","CAPPL:LOCAL.L_pu[0].einschaltfuehler_soll"),
 ("PU1 Mitte TPM","CAPPL:LOCAL.L_pu[0].ausschaltfuehler_ist","CAPPL:LOCAL.L_pu[0].ausschaltfuehler_soll"),
 ("PU1 Pumpe","CAPPL:LOCAL.L_pu[0].pumpe",None),
 ("WW1 Temperatur","CAPPL:LOCAL.L_ww[0].einschaltfuehler_ist","CAPPL:LOCAL.L_ww[0].temp_soll"),
 ("WW1 Pumpe","CAPPL:LOCAL.L_ww[0].pumpe",None),
 ("HK1 Vorlauftemperatur","CAPPL:LOCAL.L_hk[0].vorlauftemp_ist","CAPPL:LOCAL.L_hk[0].vorlauftemp_soll"),
 ("HK2 Vorlauftemperatur","CAPPL:LOCAL.L_hk[1].vorlauftemp_ist","CAPPL:LOCAL.L_hk[1].vorlauftemp_soll"),
 ("HK1 Raumtemperatur","CAPPL:LOCAL.L_hk[0].raumtemp_ist","CAPPL:LOCAL.L_hk[0].raumtemp_soll"),
 ("HK2 Raumtemperatur","CAPPL:LOCAL.L_hk[1].raumtemp_ist","CAPPL:LOCAL.L_hk[1].raumtemp_soll"),
 ("HK1 Pumpe","CAPPL:LOCAL.L_hk[0].pumpe",None),
 ("HK2 Pumpe","CAPPL:LOCAL.L_hk[1].pumpe",None),
 ("Kesseltemperatur PE1","CAPPL:FA[0].L_kesseltemperatur","CAPPL:FA[0].L_kesseltemperatur_soll_anzeige"),
 ("Abgastemperatur PE1","CAPPL:FA[0].L_abgastemperatur",None),
 ("Feuerraumtemperatur PE1","CAPPL:FA[0].L_feuerraumtemperatur","CAPPL:FA[0].L_feuerraumtemperatur_soll"),
 ("Kesselstatus PE1","CAPPL:FA[0].L_kesselstatus",None),
 ("Modulation PE1","CAPPL:FA[0].L_modulationsstufe",None),
 ("Unterdruck PE1","CAPPL:FA[0].L_unterdruck","CAPPL:FA[0].L_unterdruck_soll_anzeige"),
 ("Pelletfüllstand","CAPPL:FA[0].L_fuellstand_aktuell",None),
 ("Aschemenge","CAPPL:FA[0].L_aschemenge_info",None),
 ("Brennerstarts","CAPPL:FA[0].L_brennerstarts",None),
 ("Brennerlaufzeit","CAPPL:FA[0].L_brennerlaufzeit_anzeige",None),
 ("Mittlere Laufzeit","CAPPL:FA[0].L_mittlere_laufzeit",None),
 ("Akt. Temperatur","CAPPL:LOCAL.L_weather_temp",None),
 ("Durchschnittliche Temperatur Morgen","CAPPL:LOCAL.L_weather_forecast_temp",None),
 ("Akt. Bewölkung","CAPPL:LOCAL.L_weather_forecast_clouds",None),
]
OEKOFEN_INFO_VARIABLES = ["CAPPL:LOCAL.L_fernwartung_datum_zeit_sek"] + sum(
    ([f"CAPPL:LOCAL.L_fehlerlog[{i}].code", f"CAPPL:LOCAL.L_fehlerlog[{i}].date"] for i in range(10)), []
)

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
        if exc.code == 401:
            auth = oekofen_login()
            try:
                return fetch(auth["access_token"])
            except urllib.error.HTTPError as retry_exc:
                body=retry_exc.read().decode("utf-8","replace").strip()
                raise RuntimeError(f"OEKOFEN_CSV_HTTP_{retry_exc.code}: {body[:700] or retry_exc.reason}") from retry_exc
        body=exc.read().decode("utf-8","replace").strip()
        raise RuntimeError(f"OEKOFEN_CSV_HTTP_{exc.code}: {body[:700] or exc.reason}") from exc



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




def _influx_escape_measurement(value: str) -> str:
    return str(value).replace("\\","\\\\").replace(" ","\\ ").replace(",","\\,")

def _influx_escape_tag(value: str) -> str:
    return str(value).replace("\\","\\\\").replace(" ","\\ ").replace(",","\\,").replace("=","\\=")

def _influx_escape_field_key(value: str) -> str:
    return str(value).strip().replace("\\","\\\\").replace(" ","\\ ").replace(",","\\,").replace("=","\\=")

def _oekofen_parse_timestamp(row: dict[str,Any]) -> datetime | None:
    d=str(row.get("Datum") or "").strip();t=str(row.get("Zeit") or "").strip()
    if not d or not t:return None
    for fmt in ("%d.%m.%Y %H:%M:%S","%d.%m.%Y %H:%M"):
        try:return datetime.strptime(d+" "+t,fmt).replace(tzinfo=ZoneInfo("Europe/Vienna"))
        except ValueError:pass
    return None

def _oekofen_field_value(value: Any) -> str | None:
    text=str(value or "").strip()
    if not text:return None
    normalized=text.replace(",",".")
    try:
        number=float(normalized)
        if number != number or number in (float("inf"),float("-inf")):return None
        return repr(number)
    except ValueError:
        escaped=text.replace("\\","\\\\").replace('"','\\"').replace("\n"," ")
        return '"'+escaped+'"'

def write_oekofen_csv_to_influx(plant_id: str, serial_number: str | None, raw: bytes) -> dict[str,Any]:
    text,encoding=decode_oekofen_csv(raw);csv.field_size_limit(10*1024*1024)
    reader=csv.DictReader(io.StringIO(text),delimiter=";")
    if not reader.fieldnames:raise ValueError("OekoFEN CSV has no header")
    headers=[str(x or "").strip() for x in reader.fieldnames]
    rows=[];tz=ZoneInfo("Europe/Vienna")
    for source in reader:
        row={str(k or "").strip():v for k,v in source.items() if k is not None}
        ts=_oekofen_parse_timestamp(row)
        if ts is None:continue
        fields=[]
        for key in headers:
            if key in ("Datum","Zeit") or not key:continue
            encoded=_oekofen_field_value(row.get(key))
            if encoded is not None:fields.append(_influx_escape_field_key(key)+"="+encoded)
        if not fields:continue
        tags="plant_id="+_influx_escape_tag(plant_id)
        if serial_number:tags+=",serial_number="+_influx_escape_tag(serial_number)
        rows.append("oekofen_csv,"+tags+" "+",".join(fields)+" "+str(int(ts.timestamp()*1_000_000_000)))
    if not rows:raise ValueError("OekoFEN CSV contains no importable rows")
    token=INFLUX_TOKEN.read_text().strip();query=urlencode({"org":INFLUX_ORG,"bucket":INFLUX_BUCKET,"precision":"ns"})
    # Keep requests comfortably below common proxy/body limits.
    for start in range(0,len(rows),250):
        body="\n".join(rows[start:start+250]).encode("utf-8")
        req=Request(INFLUX_URL+"/api/v2/write?"+query,data=body,method="POST",headers={"Authorization":f"Token {token}","Content-Type":"text/plain; charset=utf-8"})
        with urlopen(req,timeout=20) as response:response.read()
    return {"rows":len(rows),"columns":len(headers),"encoding":encoding,"bytes":len(raw)}

def import_oekofen_csv_day(plant_id: str, day: str, force: bool=False) -> dict[str,Any]:
    with db() as con:
        plant=con.execute("SELECT plant_id,plant_name,serial_number FROM oekofen_plants WHERE plant_id=?",(plant_id,)).fetchone()
        if plant is None:raise ValueError("OekoFEN plant not found")
        previous=con.execute("SELECT status FROM oekofen_csv_imports WHERE plant_id=? AND day=?",(plant_id,day)).fetchone()
        if previous and previous["status"]=="SUCCESS" and not force:return {"plant_id":plant_id,"day":day,"status":"SKIPPED_ALREADY_IMPORTED"}
        started=datetime.now(timezone.utc).isoformat()
        con.execute("""INSERT INTO oekofen_csv_imports(plant_id,day,status,started_at,error)
            VALUES(?,?,'RUNNING',?,NULL) ON CONFLICT(plant_id,day) DO UPDATE SET
            status='RUNNING',started_at=excluded.started_at,completed_at=NULL,error=NULL""",(plant_id,day,started))
    try:
        raw=oekofen_fetch_csv(plant_id,day)
        result=write_oekofen_csv_to_influx(plant_id,plant["serial_number"],raw)
        completed=datetime.now(timezone.utc).isoformat()
        with db() as con:
            con.execute("""UPDATE oekofen_csv_imports SET status='SUCCESS',rows_imported=?,columns_found=?,
                bytes_downloaded=?,encoding=?,completed_at=?,error=NULL WHERE plant_id=? AND day=?""",
                (result["rows"],result["columns"],result["bytes"],result["encoding"],completed,plant_id,day))
        return {"plant_id":plant_id,"plant_name":plant["plant_name"],"day":day,"status":"SUCCESS",**result}
    except Exception as exc:
        with db() as con:
            con.execute("UPDATE oekofen_csv_imports SET status='ERROR',completed_at=?,error=? WHERE plant_id=? AND day=?",
                (datetime.now(timezone.utc).isoformat(),str(exc)[:1000],plant_id,day))
        raise

def import_oekofen_previous_day(force: bool=False) -> dict[str,Any]:
    day=(datetime.now(ZoneInfo("Europe/Vienna")).date()-timedelta(days=1)).isoformat()
    with db() as con:
        plants=[dict(r) for r in con.execute("SELECT plant_id,plant_name FROM oekofen_plants ORDER BY plant_name COLLATE NOCASE")]
    results=[];errors=[]
    for plant in plants:
        try:results.append(import_oekofen_csv_day(plant["plant_id"],day,force))
        except Exception as exc:
            errors.append({"plant_id":plant["plant_id"],"plant_name":plant["plant_name"],"error":str(exc)})
    return {"day":day,"plants":len(plants),"success":sum(1 for r in results if r["status"]=="SUCCESS"),"skipped":sum(1 for r in results if r["status"].startswith("SKIPPED")),"errors":errors,"results":results}

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
        "heat_pump.electrical_power",
        "heat_pump.thermal_power",
        "heat_pump.flow_temperature",
        "heat_pump.return_temperature",
        "heat_pump.source_temperature",
        "power_to_heat.electrical_power",
        "pellet_boiler.current_power",
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



def influx_consumption_forecast(installation_id: str, hours: int = 24) -> dict[str, Any]:
    """Manufacturer-neutral base-load forecast with synchronized 15-minute energy flows."""
    hours=max(1,min(int(hours),48))
    token=INFLUX_TOKEN.read_text().strip()
    safe_id=installation_id.replace('"','\\"')
    fields=("pv.power","grid.power","battery.power","power_to_heat.electrical_power")
    field_filter=" or ".join(f'r._field == "{field}"' for field in fields)
    # Pivot in Flux: all fields of one 15-minute bucket become one row. This avoids
    # accidentally combining PV/grid from one bucket with a missing controllable load.
    flux=f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "ins_ei_telemetry")
  |> filter(fn: (r) => r.installation_id == "{safe_id}")
  |> filter(fn: (r) => {field_filter})
  |> aggregateWindow(every: 15m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time","pv.power","grid.power","battery.power","power_to_heat.electrical_power"])
"""
    request=Request(INFLUX_URL+"/api/v2/query?org="+INFLUX_ORG,data=json.dumps({"query":flux,"type":"flux"}).encode(),method="POST",headers={"Authorization":f"Token {token}","Content-Type":"application/json","Accept":"application/csv"})
    raw=urlopen(request,timeout=20).read().decode()
    tz=ZoneInfo("Europe/Vienna")
    buckets={}
    days=set()
    valid_points=0
    rejected_points=0
    for row in csv.DictReader(io.StringIO(raw)):
        ts=row.get("_time")
        try:
            pv=float(row.get("pv.power",""))
            grid=float(row.get("grid.power",""))
        except (TypeError,ValueError):
            continue
        try: battery=float(row.get("battery.power") or 0.0)
        except (TypeError,ValueError): battery=0.0
        try: p2h=float(row.get("power_to_heat.electrical_power") or 0.0)
        except (TypeError,ValueError): p2h=0.0
        try: dt=datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(tz)
        except (AttributeError,ValueError): continue
        total=max(0.0,pv+grid+battery)
        base=max(0.0,total-max(0.0,p2h))
        # Learning guardrail: reject impossible residuals instead of teaching them.
        if base>5000:
            rejected_points+=1
            continue
        buckets.setdefault((dt.weekday(),dt.hour),[]).append(base)
        days.add(dt.date())
        valid_points+=1
    all_values=sorted(value for items in buckets.values() for value in items if value>0)
    if all_values:
        mid=len(all_values)//2
        global_median=all_values[mid] if len(all_values)%2 else (all_values[mid-1]+all_values[mid])/2
    else:
        global_median=500.0
    now=datetime.now(tz)
    slots=[]
    for n in range(hours):
        slot_start=now.replace(minute=0,second=0,microsecond=0)+timedelta(hours=n)
        vals=list(buckets.get((slot_start.weekday(),slot_start.hour),[]))
        if len(vals)<4:
            vals=[value for (wd,hour),items in buckets.items() if hour==slot_start.hour for value in items]
        vals=sorted(v for v in vals if v>0)
        source="PROFILE"
        if vals:
            mid=len(vals)//2
            estimate_w=vals[mid] if len(vals)%2 else (vals[mid-1]+vals[mid])/2
            slot_quality="GOOD" if len(vals)>=8 else "LEARNING"
        else:
            # Missing history is unknown consumption, never zero consumption.
            neighbours=[]
            for delta in (-2,-1,1,2):
                h=(slot_start.hour+delta)%24
                neighbours.extend(v for (wd,hour),items in buckets.items() if hour==h for v in items if v>0)
            neighbours=sorted(neighbours)
            if neighbours:
                mid=len(neighbours)//2
                estimate_w=neighbours[mid] if len(neighbours)%2 else (neighbours[mid-1]+neighbours[mid])/2
                source="NEIGHBOUR_FALLBACK"
            else:
                estimate_w=global_median
                source="GLOBAL_FALLBACK"
            slot_quality="FALLBACK"
        # Conservative floor avoids artificial zero-load hours while learning.
        estimate_w=max(100.0,estimate_w)
        slots.append({"start":slot_start.isoformat(),"kwh":round(estimate_w/1000.0,3),"samples":len(vals),"quality":slot_quality,"source":source})
    learned_days=len(days)
    quality="GOOD" if learned_days>=21 else ("MEDIUM" if learned_days>=7 else "LEARNING")
    return {
        "installation_id":installation_id,
        "model":"INS_EI_BASE_LOAD_PROFILE_V4",
        "quality":quality,
        "learned_days":learned_days,
        "valid_points":valid_points,
        "rejected_points":rejected_points,
        "fallback_floor_w":100,
        "global_median_w":round(global_median,1),
        "hours":hours,
        "controllable_loads_excluded":["power_to_heat.electrical_power"],
        "total_kwh":round(sum(x["kwh"] for x in slots),3),
        "slots":slots,
    }


def installation_location(installation_id: str) -> dict[str, Any] | None:
    """Resolve an INS-EI telemetry id to the stored customer/installation address and geocode it."""
    with db() as con:
        telemetry=con.execute("""SELECT latitude,longitude,elevation,time_zone,location_name
            FROM telemetry_installations WHERE installation_id=?""",(installation_id,)).fetchone()
        if telemetry and telemetry["latitude"] is not None and telemetry["longitude"] is not None:
            return {"latitude":float(telemetry["latitude"]),"longitude":float(telemetry["longitude"]),
                    "elevation":telemetry["elevation"],"time_zone":telemetry["time_zone"],
                    "name":telemetry["location_name"] or installation_id,"source":"HOME_ASSISTANT"}
        row=con.execute("""
            SELECT COALESCE(NULLIF(i.address,''),c.address) address,
                   COALESCE(NULLIF(i.postal_code,''),c.postal_code) postal_code,
                   COALESCE(NULLIF(i.city,''),c.city) city,
                   COALESCE(c.country,'AT') country
            FROM devices d
            JOIN installations i ON i.id=d.installation_id
            LEFT JOIN customers c ON c.id=i.customer_id
            WHERE d.ins_installation_id=?
            ORDER BY d.id LIMIT 1
        """,(installation_id,)).fetchone()
        if row is None:
            row=con.execute("""
                SELECT COALESCE(NULLIF(i.address,''),c.address) address,
                       COALESCE(NULLIF(i.postal_code,''),c.postal_code) postal_code,
                       COALESCE(NULLIF(i.city,''),c.city) city,
                       COALESCE(c.country,'AT') country
                FROM ins_ei_installations ie
                JOIN installations i ON i.id=ie.installation_id
                LEFT JOIN customers c ON c.id=i.customer_id
                WHERE ie.ins_installation_id=?
                LIMIT 1
            """,(installation_id,)).fetchone()
    if row is None or not row["city"]:
        return None
    query=" ".join(str(x) for x in (row["postal_code"],row["city"],row["country"]) if x)
    url="https://geocoding-api.open-meteo.com/v1/search?"+urlencode({"name":query,"count":1,"language":"de","format":"json"})
    try:
        with urlopen(Request(url,headers={"User-Agent":"INS-EI/1.0"}),timeout=8) as response:
            data=json.loads(response.read().decode())
        hit=(data.get("results") or [None])[0]
        if not hit:
            # City-only fallback handles geocoders that dislike combined postal queries.
            url="https://geocoding-api.open-meteo.com/v1/search?"+urlencode({"name":row["city"],"count":1,"language":"de","format":"json","countryCode":row["country"]})
            with urlopen(Request(url,headers={"User-Agent":"INS-EI/1.0"}),timeout=8) as response:
                data=json.loads(response.read().decode())
            hit=(data.get("results") or [None])[0]
        if hit:
            return {"latitude":float(hit["latitude"]),"longitude":float(hit["longitude"]),"name":hit.get("name") or row["city"],"source":"CUSTOMER_ADDRESS_GEOCODE"}
    except Exception:
        return None
    return None


def pv_weather_hours(location: dict[str, Any], hours: int) -> dict[str, dict[str, float]]:
    """Fetch hourly cloud cover and shortwave radiation for the learned site."""
    params={
        "latitude":location["latitude"],"longitude":location["longitude"],
        "hourly":"cloud_cover,shortwave_radiation","forecast_days":3,
        "timezone":"Europe/Vienna",
    }
    url="https://api.open-meteo.com/v1/forecast?"+urlencode(params)
    with urlopen(Request(url,headers={"User-Agent":"INS-EI/1.0"}),timeout=10) as response:
        data=json.loads(response.read().decode())
    hourly=data.get("hourly") or {}
    result={}
    for ts,cloud,rad in zip(hourly.get("time",[]),hourly.get("cloud_cover",[]),hourly.get("shortwave_radiation",[])):
        result[ts]={"cloud_cover":float(cloud or 0),"shortwave_radiation":float(rad or 0)}
    return result


def influx_pv_forecast(installation_id: str, hours: int = 24) -> dict[str, Any]:
    """Self-learning PV forecast corrected by weather at the installation location."""
    hours=max(1,min(int(hours),48))
    token=INFLUX_TOKEN.read_text().strip()
    safe_id=installation_id.replace('"','\\"')
    flux=f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "ins_ei_telemetry")
  |> filter(fn: (r) => r.installation_id == "{safe_id}")
  |> filter(fn: (r) => r._field == "pv.power")
  |> aggregateWindow(every: 15m, fn: mean, createEmpty: false)
  |> keep(columns: ["_time","_value"])
"""
    request=Request(INFLUX_URL+"/api/v2/query?org="+INFLUX_ORG,data=json.dumps({"query":flux,"type":"flux"}).encode(),method="POST",headers={"Authorization":f"Token {token}","Content-Type":"application/json","Accept":"application/csv"})
    raw=urlopen(request,timeout=20).read().decode()
    tz=ZoneInfo("Europe/Vienna")
    by_hour={}
    days=set()
    for row in csv.DictReader(io.StringIO(raw)):
        ts,value=row.get("_time"),row.get("_value")
        try:
            watts=max(0.0,float(value));dt=datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(tz)
        except (TypeError,ValueError,AttributeError): continue
        by_hour.setdefault(dt.hour,[]).append(watts);days.add(dt.date())
    location=installation_location(installation_id)
    weather={}
    if location:
        try: weather=pv_weather_hours(location,hours)
        except Exception: weather={}
    now=datetime.now(tz)
    slots=[]
    for n in range(hours):
        slot_start=now.replace(minute=0,second=0,microsecond=0)+timedelta(hours=n)
        vals=sorted(by_hour.get(slot_start.hour,[]))
        if vals:
            mid=len(vals)//2
            learned_w=vals[mid] if len(vals)%2 else (vals[mid-1]+vals[mid])/2
        else: learned_w=0.0
        wx=weather.get(slot_start.strftime("%Y-%m-%dT%H:%M"))
        estimate_w=learned_w
        weather_factor=1.0
        if wx and learned_w>0:
            # Conservative V2 attenuation: the learned curve defines the site's real
            # geometry/capacity; cloud cover only corrects it downward. Radiation is
            # retained as an observable for later self-calibration.
            cloud=max(0.0,min(100.0,wx["cloud_cover"]))
            weather_factor=max(0.18,1.0-0.0075*cloud)
            estimate_w=learned_w*weather_factor
        if estimate_w<50: estimate_w=0.0
        slot_quality="GOOD" if len(vals)>=12 and len(days)>=7 and wx else "LEARNING"
        slots.append({
            "start":slot_start.isoformat(),"kwh":round(estimate_w/1000.0,3),
            "baseline_kwh":round(learned_w/1000.0,3),"samples":len(vals),
            "quality":slot_quality,"source":"HISTORICAL_PV_PLUS_WEATHER" if wx else "HISTORICAL_PV_PROFILE",
            "cloud_cover_pct":round(wx["cloud_cover"],1) if wx else None,
            "shortwave_radiation_w_m2":round(wx["shortwave_radiation"],1) if wx else None,
            "weather_factor":round(weather_factor,3) if wx else None,
        })
    learned_days=len(days)
    model_quality="GOOD" if learned_days>=21 and weather else ("MEDIUM" if learned_days>=7 and weather else "LEARNING")
    return {
        "installation_id":installation_id,"model":"INS_EI_PV_PROFILE_V2",
        "quality":model_quality,"learned_days":learned_days,"hours":hours,
        "total_kwh":round(sum(x["kwh"] for x in slots),3),
        "baseline_total_kwh":round(sum(x["baseline_kwh"] for x in slots),3),
        "method":"SELF_LEARNED_PV_HISTORY_PLUS_WEATHER" if weather else "SELF_LEARNED_PV_HISTORY",
        "external_vendor_forecast_used":False,
        "weather_used":bool(weather),"location":location,"slots":slots,
    }

def evaluate_fleet_health(installation_id: str, age_seconds: int, health: dict[str, Any], row: sqlite3.Row) -> tuple[str,list[str]]:
    issues=[]
    collector=health.get("collector") or {};forecast=health.get("server_forecast") or {};planner=health.get("planner") or {}
    if age_seconds>180: issues.append("TELEMETRY_OFFLINE")
    if collector.get("unavailable",0): issues.append("COLLECTOR_UNAVAILABLE")
    if collector.get("stale",0): issues.append("COLLECTOR_STALE")
    if health and not forecast.get("connected"): issues.append("SERVER_FORECAST_UNAVAILABLE")
    if health and planner.get("status") not in (None,"SHADOW_V1"): issues.append("PLANNER_"+str(planner.get("status")))
    status="CRITICAL" if "TELEMETRY_OFFLINE" in issues else ("WARNING" if issues else ("HEALTHY" if health else "UNKNOWN"))
    return status,issues


def fleet_transition(installation_id: str, new_status: str, issues: list[str], addon_version: str | None):
    """Persist state transitions and push only meaningful, debounced changes."""
    now=datetime.now(timezone.utc);now_iso=now.isoformat()
    with db() as con:
        row=con.execute("SELECT * FROM telemetry_installations WHERE installation_id=?",(installation_id,)).fetchone()
        if row is None:return
        old=row["fleet_status"] or "UNKNOWN";since=row["fleet_status_since"]
        push_enabled=bool(row["fleet_push_enabled"]) if "fleet_push_enabled" in row.keys() else True
        old_version=row["addon_version"]
        if addon_version and old_version and addon_version!=old_version:
            con.execute("UPDATE telemetry_installations SET previous_addon_version=?,version_changed_at=? WHERE installation_id=?",(old_version,now_iso,installation_id))
        if new_status!=old:
            con.execute("UPDATE telemetry_installations SET fleet_status=?,fleet_status_since=? WHERE installation_id=?",(new_status,now_iso,installation_id))
            # CRITICAL is immediate; WARNING is shown in GUI first and pushed only
            # if it persists on a later health cycle. Recovery is pushed once.
            if new_status=="CRITICAL" and push_enabled:
                send_native_push_all(f"INS-EI · {installation_id} · CRITICAL"," · ".join(issues) or "Instanz kritisch","/#instances")
                con.execute("UPDATE telemetry_installations SET fleet_notified_status=? WHERE installation_id=?",(new_status,installation_id))
            elif new_status=="HEALTHY" and old in ("WARNING","CRITICAL") and push_enabled:
                send_native_push_all(f"INS-EI · {installation_id} · HEALTHY","Instanz wieder gesund.","/#instances")
                con.execute("UPDATE telemetry_installations SET fleet_notified_status=? WHERE installation_id=?",(new_status,installation_id))
        elif new_status=="WARNING" and since:
            try: persistent=(now-datetime.fromisoformat(since)).total_seconds()>=300
            except ValueError:persistent=False
            if persistent and row["fleet_notified_status"]!="WARNING" and push_enabled:
                send_native_push_all(f"INS-EI · {installation_id} · WARNING"," · ".join(issues) or "Warnung besteht seit mindestens 5 Minuten","/#instances")
                con.execute("UPDATE telemetry_installations SET fleet_notified_status='WARNING' WHERE installation_id=?",(installation_id,))


def telemetry_status() -> dict[str, Any]:
    """Return status of all INS-EI telemetry installations."""

    now = datetime.now(timezone.utc)
    installations = []

    with db() as con:
        rows = con.execute(
            """
            SELECT ti.*,
                   (SELECT status FROM fleet_update_commands c
                    WHERE c.installation_id=ti.installation_id
                    ORDER BY c.id DESC LIMIT 1) AS update_status,
                   (SELECT target_version FROM fleet_update_commands c
                    WHERE c.installation_id=ti.installation_id
                    ORDER BY c.id DESC LIMIT 1) AS update_target_version,
                   (SELECT id FROM fleet_update_commands c
                    WHERE c.installation_id=ti.installation_id
                    ORDER BY c.id DESC LIMIT 1) AS update_command_id,
                   (SELECT error FROM fleet_update_commands c
                    WHERE c.installation_id=ti.installation_id
                    ORDER BY c.id DESC LIMIT 1) AS update_error
            FROM telemetry_installations ti
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

        health={}
        try: health=json.loads(row["health_json"] or "{}")
        except (TypeError,json.JSONDecodeError): pass
        fleet_status,issues=evaluate_fleet_health(row["installation_id"],age_seconds,health,row)
        with db() as con:
            agent=con.execute("SELECT * FROM fleet_update_agents WHERE installation_id=?",(row["installation_id"],)).fetchone()
        agent_online=False
        if agent:
            try: agent_online=(now-datetime.fromisoformat(agent["last_seen_at"])).total_seconds()<=180
            except (TypeError,ValueError): pass
        installations.append({
            "installation_id": row["installation_id"],
            "current": current,
            "fleet_status":fleet_status,"issues":issues,"health":health,"addon_version":row["addon_version"],
            "update_status":row["update_status"],"update_target_version":row["update_target_version"],
            "update_command_id":row["update_command_id"],"update_error":row["update_error"],
            "update_agent_version":agent["agent_version"] if agent else None,"update_agent_online":agent_online,
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
    site: dict[str, Any] = Field(default_factory=dict)
    health: dict[str, Any] = Field(default_factory=dict)


def version_tuple(value: str | None) -> tuple[int, ...] | None:
    """Parse numeric dotted add-on versions for monotonic fleet comparisons."""
    if not value: return None
    raw=value.strip().lstrip("v")
    try: return tuple(int(part) for part in raw.split("."))
    except (TypeError,ValueError): return None


class FleetUpdateAgentHeartbeat(BaseModel):
    agent_version: str


class FleetUpdateRequest(BaseModel):
    target_version: str


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


def oekofen_csv_worker():
    """Import yesterday once daily after 03:00 Europe/Vienna, retrying failures hourly."""
    last_attempt_hour=None
    while True:
        try:
            now=datetime.now(ZoneInfo("Europe/Vienna"))
            key=now.strftime("%Y-%m-%d-%H")
            if now.hour in (3,6,12,18) and key!=last_attempt_hour:
                day=(now.date()-timedelta(days=1)).isoformat()
                with db() as con:
                    pending=con.execute("""SELECT COUNT(*) AS n FROM oekofen_plants p
                        LEFT JOIN oekofen_csv_imports i ON i.plant_id=p.plant_id AND i.day=?
                        WHERE i.status IS NULL OR i.status!='SUCCESS'""",(day,)).fetchone()["n"]
                if pending:
                    last_attempt_hour=key
                    result=import_oekofen_previous_day(False)
                    print(f"OEKOFEN CSV day={result['day']} plants={result['plants']} success={result['success']} skipped={result['skipped']} errors={len(result['errors'])}",flush=True)
        except Exception as exc:
            print(f"OEKOFEN CSV worker error: {exc}",flush=True)
        time.sleep(300)


def oekofen_worker():
    while True:
        try:
            result=sync_oekofen_plants()
            print(f"OEKOFEN sync plants={result.get('plants',0)} problems={result.get('problems',0)}",flush=True)
        except Exception as exc:
            print(f"OEKOFEN sync error: {exc}",flush=True)
        time.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_customer_db()
    init_portal_db()
    init_service_auth_db()
    _ensure_portal_admin()
    init_oekofen_db()

    mqtt_thread = threading.Thread(
        target=mqtt_live_worker,
        daemon=True,
        name="mqtt-bus-v1",
    )
    mqtt_thread.start()

    thread = threading.Thread(
        target=reminder_worker,
        daemon=True,
        name="reminder-worker",
    )
    thread.start()

    oekofen_thread = threading.Thread(
        target=oekofen_worker,
        daemon=True,
        name="oekofen-worker",
    )
    oekofen_thread.start()

    oekofen_csv_thread = threading.Thread(
        target=oekofen_csv_worker,
        daemon=True,
        name="oekofen-csv-worker",
    )
    oekofen_csv_thread.start()

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



def _dir_size(path: Path) -> int:
    total=0
    try:
        for root,dirs,files in os.walk(path):
            for name in files:
                try: total += (Path(root)/name).stat().st_size
                except OSError: pass
    except OSError:
        pass
    return total

def _host_mem() -> tuple[int,int]:
    vals={}
    try:
        for line in Path("/host/proc/meminfo").read_text().splitlines():
            key,val=line.split(":",1);vals[key]=int(val.strip().split()[0])*1024
    except Exception:
        return 0,0
    total=vals.get("MemTotal",0);avail=vals.get("MemAvailable",0)
    return total,max(0,total-avail)

def _host_cpu_sample() -> tuple[int,int]:
    try:
        parts=Path("/host/proc/stat").read_text().splitlines()[0].split()[1:]
        nums=[int(x) for x in parts]
        idle=nums[3]+(nums[4] if len(nums)>4 else 0)
        return sum(nums),idle
    except Exception:
        return 0,0

@app.get("/api/v1/system/metrics")
def system_metrics():
    total1,idle1=_host_cpu_sample();time.sleep(0.12);total2,idle2=_host_cpu_sample()
    delta=max(0,total2-total1);idle=max(0,idle2-idle1)
    cpu=round(100*(delta-idle)/delta,1) if delta else None
    mem_total,mem_used=_host_mem()
    try:
        disk=shutil.disk_usage("/host/root")
        disk_total,disk_used,disk_free=disk.total,disk.used,disk.free
    except OSError:
        disk_total=disk_used=disk_free=0
    sqlite_size=DB_PATH.stat().st_size if DB_PATH.exists() else 0
    influx_size=_dir_size(Path("/influx-data"))
    try:
        uptime=float(Path("/host/proc/uptime").read_text().split()[0])
    except Exception:
        uptime=None
    return {
        "timestamp":datetime.now(timezone.utc).isoformat(),
        "cpu_percent":cpu,
        "memory":{"total":mem_total,"used":mem_used,"percent":round(100*mem_used/mem_total,1) if mem_total else None},
        "disk":{"total":disk_total,"used":disk_used,"free":disk_free,"percent":round(100*disk_used/disk_total,1) if disk_total else None},
        "databases":{"influx_bytes":influx_size,"sqlite_bytes":sqlite_size},
        "uptime_seconds":uptime,
        "services":{"api":"OK","mqtt":"OK" if MQTT_SERVICE_CLIENT is not None else "UNKNOWN","influx":"OK" if INFLUX_TOKEN.exists() else "UNKNOWN"},
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "ins-ei-api",
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }




@app.get("/api/v1/oekofen/csv-imports")
def api_oekofen_csv_imports(day: str | None = None):
    with db() as con:
        if day:
            rows=con.execute("""SELECT i.*,p.plant_name,p.serial_number FROM oekofen_csv_imports i
                LEFT JOIN oekofen_plants p ON p.plant_id=i.plant_id WHERE i.day=? ORDER BY p.plant_name COLLATE NOCASE""",(day,)).fetchall()
        else:
            rows=con.execute("""SELECT i.*,p.plant_name,p.serial_number FROM oekofen_csv_imports i
                LEFT JOIN oekofen_plants p ON p.plant_id=i.plant_id ORDER BY i.day DESC,p.plant_name COLLATE NOCASE LIMIT 250""").fetchall()
    return {"imports":[dict(r) for r in rows]}

@app.post("/api/v1/oekofen/csv-import/{plant_id}")
def api_oekofen_csv_import_one(plant_id: str, day: str, force: bool=False):
    try:return import_oekofen_csv_day(plant_id,day,force)
    except Exception as exc:raise HTTPException(502,str(exc))

@app.post("/api/v1/oekofen/csv-import-previous-day")
def api_oekofen_csv_import_previous_day(force: bool=False):
    return import_oekofen_previous_day(force)


@app.get("/api/v1/oekofen/plants")
def api_oekofen_plants():
    with db() as con:
        rows=con.execute("""SELECT p.*,d.id device_id,d.installation_id,
          d.manufacturer device_manufacturer,d.model device_model,
          i.customer_id,c.name customer_name,c.city customer_city
          FROM oekofen_plants p
          LEFT JOIN devices d ON d.oekofen_plant_id=p.plant_id
          LEFT JOIN installations i ON i.id=d.installation_id
          LEFT JOIN customers c ON c.id=i.customer_id
          ORDER BY p.problem_count DESC,p.plant_name COLLATE NOCASE""").fetchall()
        result=[]
        for row in rows:
            x=dict(row)
            problems=con.execute("""SELECT problem_type,message,create_date
              FROM oekofen_problems WHERE plant_id=? ORDER BY create_date DESC,id DESC""",
              (row["plant_id"],)).fetchall()
            x["problems"]=[dict(p) for p in problems]
            yesterday=(datetime.now(ZoneInfo("Europe/Vienna")).date()-timedelta(days=1)).isoformat()
            csv_status=con.execute("""SELECT status,rows_imported,columns_found,completed_at,error
                FROM oekofen_csv_imports WHERE plant_id=? AND day=?""",(row["plant_id"],yesterday)).fetchone()
            x["csv_day"]=yesterday
            x["csv_import"]=dict(csv_status) if csv_status else None
            result.append(x)
    return {"plants":result,"sync_interval_seconds":300}


class OekofenPlantSettings(BaseModel):
    visible_in_overview: bool = True
    monitoring_enabled: bool = True
    push_enabled: bool = False
    analysis_enabled: bool = False


@app.put("/api/v1/oekofen/plants/{plant_id}/settings")
def api_oekofen_settings(plant_id: str,item: OekofenPlantSettings):
    visible=bool(item.visible_in_overview)
    monitoring=bool(item.monitoring_enabled) if visible else False
    push=bool(item.push_enabled) if visible else False
    analysis=bool(item.analysis_enabled) if visible else False
    with db() as con:
        cur=con.execute("""UPDATE oekofen_plants SET visible_in_overview=?,monitoring_enabled=?,
            push_enabled=?,analysis_enabled=? WHERE plant_id=?""",
            (int(visible),int(monitoring),int(push),int(analysis),plant_id))
        if cur.rowcount==0: raise HTTPException(404,"OekoFEN plant not found")
    return {"status":"updated","plant_id":plant_id,"visible_in_overview":visible,
            "monitoring_enabled":monitoring,"push_enabled":push,"analysis_enabled":analysis}


class OekofenPlantLink(BaseModel):
    customer_id: int | None = None
    device_id: int | None = None


@app.put("/api/v1/oekofen/plants/{plant_id}/link")
def api_oekofen_link(plant_id: str,item: OekofenPlantLink):
    with db() as con:
        if con.execute("SELECT 1 FROM oekofen_plants WHERE plant_id=?",(plant_id,)).fetchone() is None:
            raise HTTPException(404,"OekoFEN plant not found")
        con.execute("UPDATE devices SET oekofen_plant_id=NULL,updated_at=? WHERE oekofen_plant_id=?",
                    (datetime.now(timezone.utc).isoformat(),plant_id))
        device_id=item.device_id
        if device_id is None and item.customer_id is not None:
            customer=con.execute("SELECT id,name FROM customers WHERE id=?",(item.customer_id,)).fetchone()
            if customer is None: raise HTTPException(404,"Customer not found")
            plant=con.execute("SELECT plant_name,serial_number FROM oekofen_plants WHERE plant_id=?",(plant_id,)).fetchone()
            serial=(plant["serial_number"] or "").strip()
            if serial:
                match=con.execute("""SELECT d.id FROM devices d JOIN installations i ON i.id=d.installation_id
                    WHERE i.customer_id=? AND LOWER(COALESCE(d.serial_number,''))=LOWER(?) LIMIT 1""",
                    (item.customer_id,serial)).fetchone()
                if match: device_id=match["id"]
            if device_id is None:
                installation=con.execute("SELECT id FROM installations WHERE customer_id=? ORDER BY id LIMIT 1",(item.customer_id,)).fetchone()
                now=datetime.now(timezone.utc).isoformat()
                if installation is None:
                    cur=con.execute("""INSERT INTO installations(customer_id,name,installation_type,created_at,updated_at)
                        VALUES (?,?,?,?,?)""",(item.customer_id,"Anlage "+customer["name"],"heating",now,now))
                    installation_id=cur.lastrowid
                else: installation_id=installation["id"]
                model=(plant["plant_name"] or "ÖkoFEN").strip()
                cur=con.execute("""INSERT INTO devices
                    (installation_id,device_type,manufacturer,model,serial_number,maintenance_interval_months,
                     online_capable,oekofen_plant_id,created_at,updated_at)
                    VALUES (?,?,?,?,?,12,1,?,?,?)""",
                    (installation_id,"heating","ÖkoFEN",model,serial,plant_id,now,now))
                device_id=cur.lastrowid
        if device_id is not None:
            plant=con.execute("SELECT plant_name,serial_number,version FROM oekofen_plants WHERE plant_id=?",(plant_id,)).fetchone()
            if plant is None: raise HTTPException(404,"OekoFEN plant not found")
            model=(plant["plant_name"] or "").strip() or None
            serial=(plant["serial_number"] or "").strip() or None
            cur=con.execute("""UPDATE devices SET oekofen_plant_id=?,manufacturer='ÖkoFEN',
                model=COALESCE(?,model),serial_number=COALESCE(?,serial_number),updated_at=? WHERE id=?""",
                (plant_id,model,serial,datetime.now(timezone.utc).isoformat(),device_id))
            if cur.rowcount==0: raise HTTPException(404,"Device not found")
    return {"status":"updated","plant_id":plant_id,"device_id":device_id,"synced_fields":["manufacturer","model","serial_number"] if device_id is not None else []}


@app.get("/api/v1/oekofen/plants/{plant_id}/measurements")
def api_oekofen_measurements(plant_id: str):
    names=[]
    for _,actual,target in OEKOFEN_MEASUREMENTS:
        names.append(actual)
        if target: names.append(target)
    values=oekofen_fetch_variables(plant_id,list(dict.fromkeys(names)))
    by_name={x.get("name"):x for x in values if isinstance(x,dict)}
    rows=[]
    for label,actual,target in OEKOFEN_MEASUREMENTS:
        av=oekofen_format_variable(by_name.get(actual))
        tv=oekofen_format_variable(by_name.get(target)) if target else None
        if av is not None or tv is not None:
            rows.append({"label":label,"actual":av,"target":tv})
    return {"plant_id":plant_id,"rows":rows}

@app.get("/api/v1/oekofen/plants/{plant_id}/infos")
def api_oekofen_infos(plant_id: str):
    values=oekofen_fetch_variables(plant_id,OEKOFEN_INFO_VARIABLES)
    by_name={x.get("name"):x for x in values if isinstance(x,dict)}
    entries=[]
    for i in range(10):
        code=by_name.get(f"CAPPL:LOCAL.L_fehlerlog[{i}].code",{}).get("value")
        date=by_name.get(f"CAPPL:LOCAL.L_fehlerlog[{i}].date",{}).get("value")
        if code and str(code) not in ("0","0.000000"):
            raw=str(code).split(".")[0].zfill(9)
            error_code=raw[0:4]
            participant=int(raw[4:6] or 0)
            status_code=int(raw[6:7] or 0)
            entries.append({"index":i,"raw_code":raw,"error_code":error_code,"participant":participant,
                "status_code":status_code,"status":{1:"Fehler",2:"bestätigt",3:"behoben"}.get(status_code,""),
                "date":str(date or "")})
    helptexts={}
    try:
        req=Request("https://my.oekofen.info/app/assets/maintenance/help/de.json",headers={"Accept":"application/json"})
        with urlopen(req,timeout=20) as response: helptexts=json.loads(response.read().decode("utf-8"))
    except Exception:
        pass
    for entry in entries:
        key=("warning_" if entry["error_code"] in ("5050","5053","5054") else "error_")+entry["error_code"]
        helpitem=helptexts.get(key) or helptexts.get("error_"+entry["error_code"]) or helptexts.get("warning_"+entry["error_code"]) or {}
        title=str(helpitem.get("title") or ("Code "+entry["error_code"]))
        title=title.replace("<%1>",str(entry["participant"]))
        entry["title"]=title
        try:
            ts=float(entry["date"])
            entry["date_iso"]=datetime.fromtimestamp(ts,timezone.utc).isoformat()
        except Exception: entry["date_iso"]=None
    return {"plant_id":plant_id,"entries":entries}

@app.post("/api/v1/oekofen/sync")
def api_oekofen_sync():
    return sync_oekofen_plants()



@app.get("/api/v1/forecast/pv/{installation_id}")
def get_pv_forecast(installation_id: str, hours: int = 24):
    try:
        return influx_pv_forecast(installation_id, hours)
    except Exception as exc:
        raise HTTPException(503, f"PV forecast unavailable: {exc}") from exc


@app.get("/api/v1/forecast/consumption/{installation_id}")
def get_consumption_forecast(installation_id: str, hours: int = 24):
    try:
        return influx_consumption_forecast(installation_id, hours)
    except Exception as exc:
        raise HTTPException(503, f"Consumption forecast unavailable: {exc}") from exc

@app.post("/api/v1/fleet/{installation_id}/update-agent/heartbeat")
def fleet_update_agent_heartbeat(installation_id: str, payload: FleetUpdateAgentHeartbeat):
    now=datetime.now(timezone.utc).isoformat()
    with db() as con:
        con.execute("""INSERT INTO fleet_update_agents(installation_id,agent_version,last_seen_at,status)
            VALUES(?,?,?,'ONLINE')
            ON CONFLICT(installation_id) DO UPDATE SET
              agent_version=excluded.agent_version,last_seen_at=excluded.last_seen_at,status='ONLINE'""",
            (installation_id,payload.agent_version,now))
    return {"status":"ok","installation_id":installation_id,"agent_version":payload.agent_version}


@app.post("/api/v1/fleet/{installation_id}/update")
def fleet_request_update(installation_id: str, payload: FleetUpdateRequest):
    now=datetime.now(timezone.utc).isoformat()
    with db() as con:
        installation=con.execute("SELECT addon_version FROM telemetry_installations WHERE installation_id=?",(installation_id,)).fetchone()
        if installation is None:
            raise HTTPException(404, f"Unknown installation: {installation_id}")
        current=installation["addon_version"]
        if current and current==payload.target_version:
            return {"status":"already_current","installation_id":installation_id,"target_version":payload.target_version}
        active=con.execute("""SELECT id,status,target_version FROM fleet_update_commands
            WHERE installation_id=? AND status IN ('PENDING','CLAIMED')
            ORDER BY id DESC LIMIT 1""",(installation_id,)).fetchone()
        if active and active["target_version"]==payload.target_version:
            return {"status":"already_queued","installation_id":installation_id,
                    "target_version":active["target_version"],"command_id":active["id"]}
        if active:
            con.execute("""UPDATE fleet_update_commands
                SET status='SUPERSEDED',completed_at=?,error=?
                WHERE installation_id=? AND status IN ('PENDING','CLAIMED')""",
                (now,f"SUPERSEDED_BY_{payload.target_version}",installation_id))
        cur=con.execute("""INSERT INTO fleet_update_commands(installation_id,target_version,status,created_at)
            VALUES(?,?, 'PENDING',?)""",(installation_id,payload.target_version,now))
        command_id=cur.lastrowid
    return {"status":"queued","installation_id":installation_id,"target_version":payload.target_version,"command_id":command_id}


@app.post("/api/v1/fleet/{installation_id}/command/requeue")
def fleet_requeue_claimed_command(installation_id: str):
    """Release the newest claimed update command so the dedicated update agent can take it."""
    with db() as con:
        row=con.execute("""SELECT id,target_version FROM fleet_update_commands
            WHERE installation_id=? AND status='CLAIMED' ORDER BY id DESC LIMIT 1""",
            (installation_id,)).fetchone()
        if row is None:
            return {"status":"nothing_to_requeue","installation_id":installation_id}
        con.execute("""UPDATE fleet_update_commands
            SET status='PENDING',claimed_at=NULL,completed_at=NULL,error='REQUEUED_FOR_UPDATE_AGENT'
            WHERE id=?""",(row["id"],))
    return {"status":"PENDING","installation_id":installation_id,
            "command_id":row["id"],"target_version":row["target_version"]}


@app.get("/api/v1/fleet/{installation_id}/command")
def fleet_get_command(installation_id: str):
    # Legacy endpoint kept intentionally empty so older INS-EI Pilot versions
    # cannot steal update commands from the dedicated Update Agent.
    return {"command":None}


@app.get("/api/v1/fleet/{installation_id}/update-agent/command")
def fleet_update_agent_get_command(installation_id: str):
    now_dt=datetime.now(timezone.utc)
    lease_cutoff=(now_dt-timedelta(minutes=3)).isoformat()
    with db() as con:
        # A claim is a short lease. If an agent/HA instance restarts mid-update,
        # the command becomes available again instead of remaining stuck forever.
        con.execute("""UPDATE fleet_update_commands
            SET status='PENDING',claimed_at=NULL,error='CLAIM_LEASE_EXPIRED'
            WHERE installation_id=? AND status='CLAIMED'
              AND (claimed_at IS NULL OR claimed_at<?)""",(installation_id,lease_cutoff))
        row=con.execute("""SELECT * FROM fleet_update_commands
            WHERE installation_id=? AND status='PENDING' ORDER BY id DESC LIMIT 1""",(installation_id,)).fetchone()
        if row is None:return {"command":None}
        now=now_dt.isoformat()
        con.execute("UPDATE fleet_update_commands SET status='CLAIMED',claimed_at=?,error=NULL WHERE id=?",(now,row["id"]))
    return {"command":{"id":row["id"],"type":"UPDATE_ADDON","target_version":row["target_version"]}}


@app.post("/api/v1/fleet/{installation_id}/command/{command_id}/result")
def fleet_command_result(installation_id: str, command_id: int, body: dict[str,Any]):
    retry=bool(body.get("retry"))
    status="PENDING" if retry else ("COMPLETED" if body.get("ok") else "FAILED")
    now=datetime.now(timezone.utc).isoformat()
    with db() as con:
        row=con.execute("SELECT * FROM fleet_update_commands WHERE id=? AND installation_id=?",
                        (command_id,installation_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Update command not found")
        if retry:
            con.execute("""UPDATE fleet_update_commands SET status='PENDING',claimed_at=NULL,completed_at=NULL,error=?
                WHERE id=? AND installation_id=?""",(body.get("error"),command_id,installation_id))
        else:
            con.execute("""UPDATE fleet_update_commands SET status=?,completed_at=?,error=?
                WHERE id=? AND installation_id=?""",(status,now,body.get("error"),command_id,installation_id))
    if status=="FAILED":
        send_native_push_all(f"INS-EI · {installation_id} · UPDATE FAILED",
                             body.get("error") or f"Update auf {row['target_version']} fehlgeschlagen.",
                             "/#instances")
    return {"status":status,"target_version":row["target_version"]}



@app.get("/api/v1/bus/{site_id}/learning")
def get_bus_learning(site_id: str, _: bool = Depends(require_management_api_key)):
    try:
        return bus_snapshot(site_id,"learning")
    except ValueError as exc:
        raise HTTPException(404,str(exc)) from exc


@app.get("/api/v1/bus/{site_id}/health")
def get_bus_health(site_id: str, _: bool = Depends(require_management_api_key)):
    try:
        return bus_snapshot(site_id,"health")
    except ValueError as exc:
        raise HTTPException(404,str(exc)) from exc


@app.get("/api/v1/live/{installation_id}")
def get_live_state(installation_id: str):
    with MQTT_LIVE_LOCK:
        item=dict(MQTT_LIVE.get(installation_id) or {})
    if not item: raise HTTPException(404,"No MQTT live state")
    received=item.get("received_at")
    age=None
    if received:
        try: age=round((datetime.now(timezone.utc)-datetime.fromisoformat(received)).total_seconds(),1)
        except ValueError: pass
    item["installation_id"]=installation_id;item["age_seconds"]=age;item["point_count"]=len(item.get("values") or {})
    return item


@app.get("/api/v1/telemetry/history/{installation_id}")
def get_telemetry_history(
    installation_id: str,
    period: str = "24h",
):
    try:
        return influx_history(installation_id, period)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/telemetry/health-history/{installation_id}")
def get_telemetry_health_history(installation_id: str, period: str = "24h"):
    periods={"24h":24,"7d":24*7,"30d":24*30}
    if period not in periods: raise HTTPException(400,"period must be 24h, 7d or 30d")
    since=(datetime.now(timezone.utc)-timedelta(hours=periods[period])).isoformat()
    with db() as con:
        rows=con.execute("""SELECT timestamp,good,stale,unavailable FROM telemetry_health_samples
            WHERE installation_id=? AND timestamp>=? ORDER BY timestamp""",(installation_id,since)).fetchall()
    samples=[dict(r) for r in rows]
    totals={"samples":len(samples),"good":sum(r["good"] for r in samples),
            "stale":sum(r["stale"] for r in samples),"unavailable":sum(r["unavailable"] for r in samples)}
    points=totals["good"]+totals["stale"]+totals["unavailable"]
    totals["good_percent"]=round(100*totals["good"]/points,2) if points else None
    with db() as con:
        issue_rows=con.execute("""SELECT point,quality,COUNT(*) AS count,MAX(timestamp) AS last_seen
            FROM telemetry_health_issues WHERE installation_id=? AND timestamp>=?
            GROUP BY point,quality ORDER BY count DESC,point""",(installation_id,since)).fetchall()
    return {"installation_id":installation_id,"period":period,"totals":totals,"samples":samples,
            "issues":[dict(r) for r in issue_rows]}


@app.post("/api/v1/telemetry/{installation_id}/fleet-push")
def set_legacy_fleet_push(installation_id: str, enabled: bool, _: bool = Depends(require_management_api_key)):
    with db() as con:
        cur=con.execute("UPDATE telemetry_installations SET fleet_push_enabled=? WHERE installation_id=?",
                        (1 if enabled else 0,installation_id))
        if cur.rowcount==0:
            raise HTTPException(404,"Telemetry installation not found")
    return {"installation_id":installation_id,"fleet_push_enabled":enabled}


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

    if payload.health:
        collector_health=payload.health.get("collector") or {}
        issues=collector_health.get("issues") or []
        if issues:
            with db() as con:
                con.executemany("""INSERT INTO telemetry_health_issues
                    (installation_id,timestamp,point,quality,source,value,unit) VALUES (?,?,?,?,?,?,?)""",
                    [(payload.installation_id,received_at,str(i.get("point") or ""),str(i.get("quality") or ""),
                      i.get("source"),None if i.get("value") is None else str(i.get("value")),i.get("unit")) for i in issues])
        with db() as con:
            con.execute("""INSERT INTO telemetry_health_samples
                (installation_id,timestamp,good,stale,unavailable)
                VALUES (?,?,?,?,?)""",
                (payload.installation_id,received_at,int(collector_health.get("good") or 0),
                 int(collector_health.get("stale") or 0),int(collector_health.get("unavailable") or 0)))
        with db() as con:
            con.execute("""UPDATE telemetry_installations
                SET health_json=?,addon_version=?,health_updated_at=?
                WHERE installation_id=?""",
                (json.dumps(payload.health,ensure_ascii=False),payload.health.get("addon_version"),received_at,payload.installation_id))
        with db() as con:
            fleet_row=con.execute("SELECT * FROM telemetry_installations WHERE installation_id=?",(payload.installation_id,)).fetchone()
            reported_version=payload.health.get("addon_version")
            reported_tuple=version_tuple(reported_version)
            if reported_tuple:
                active_updates=con.execute("""SELECT id,target_version FROM fleet_update_commands
                    WHERE installation_id=? AND status IN ('PENDING','CLAIMED')
                    ORDER BY id""",(payload.installation_id,)).fetchall()
                for update in active_updates:
                    target_tuple=version_tuple(update["target_version"])
                    if target_tuple and reported_tuple >= target_tuple:
                        con.execute("""UPDATE fleet_update_commands
                            SET status='COMPLETED',completed_at=?,error=NULL
                            WHERE id=?""",(received_at,update["id"]))
        status,issues=evaluate_fleet_health(payload.installation_id,0,payload.health,fleet_row)
        fleet_transition(payload.installation_id,status,issues,payload.health.get("addon_version"))

    if payload.site:
        lat=payload.site.get("latitude");lon=payload.site.get("longitude")
        if lat is not None and lon is not None:
            with db() as con:
                con.execute("""UPDATE telemetry_installations
                    SET latitude=?,longitude=?,elevation=?,time_zone=?,location_name=?
                    WHERE installation_id=?""",
                    (lat,lon,payload.site.get("elevation"),payload.site.get("time_zone"),
                     payload.site.get("location_name"),payload.installation_id))

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


@app.delete("/api/v1/customers/{customer_id}")
def delete_customer(customer_id: int):
    """Delete a customer and customer-owned records/files.

    Projects are preserved and detached from the customer. This avoids silently
    deleting project history that may still be operationally relevant.
    """
    file_paths=[]
    with db() as con:
        customer=con.execute("SELECT id,name FROM customers WHERE id=?",(customer_id,)).fetchone()
        if customer is None:
            raise HTTPException(404,"Customer not found")
        installations=[r["id"] for r in con.execute("SELECT id FROM installations WHERE customer_id=?",(customer_id,))]
        devices=[]
        if installations:
            marks=",".join("?" for _ in installations)
            devices=[r["id"] for r in con.execute(f"SELECT id FROM devices WHERE installation_id IN ({marks})",installations)]
        file_rows=con.execute("SELECT stored_name FROM customer_files WHERE customer_id=?",(customer_id,)).fetchall()
        file_paths=[FILES_PATH/r["stored_name"] for r in file_rows if r["stored_name"]]

        # Preserve projects, but remove their link to the deleted customer.
        con.execute("UPDATE projects SET customer_id=NULL WHERE customer_id=?",(customer_id,))
        con.execute("DELETE FROM tasks WHERE customer_id=?",(customer_id,))
        con.execute("DELETE FROM customer_credentials WHERE customer_id=?",(customer_id,))
        con.execute("DELETE FROM customer_files WHERE customer_id=?",(customer_id,))
        con.execute("DELETE FROM maintenance_jobs WHERE customer_id=?",(customer_id,))
        con.execute("DELETE FROM service_visits WHERE customer_id=?",(customer_id,))
        if devices:
            marks=",".join("?" for _ in devices)
            con.execute(f"DELETE FROM devices WHERE id IN ({marks})",devices)
        con.execute("DELETE FROM installations WHERE customer_id=?",(customer_id,))
        con.execute("DELETE FROM customers WHERE id=?",(customer_id,))

    for path in file_paths:
        try:
            if path.exists(): path.unlink()
        except OSError:
            pass
    return {"status":"deleted","id":customer_id,"name":customer["name"]}


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
    year_start = f"{date.today().year:04d}-01-01"
    year_end = f"{date.today().year + 1:04d}-01-01"
    customers = []
    with db() as con:
        for row in rows:
            item = dict(row)
            flagged = con.execute("""SELECT d.id FROM devices d
                JOIN installations i ON i.id=d.installation_id
                WHERE i.customer_id=? AND COALESCE(d.maintenance_customer,0)=1""",(row["id"],)).fetchall()
            item["maintenance_customer"] = bool(flagged)
            item["maintenance_due"] = any(
                con.execute("""SELECT 1 FROM service_visits
                    WHERE customer_id=? AND device_id=? AND visit_date>=? AND visit_date<?
                      AND (entry_type='maintenance' OR category='Wartung') LIMIT 1""",
                    (row["id"],device["id"],year_start,year_end)).fetchone() is None
                for device in flagged
            )
            customers.append(item)
    return {"count": len(customers), "customers": customers}


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
    maintenance_customer: bool = False
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
             construction_year,commissioning_date,power_kw,maintenance_interval_months,maintenance_next_due_date,maintenance_customer,online_capable,notes,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (installation_id,"heating",device.manufacturer,device.model,device.serial_number,
             device.touch_id,device.construction_year,device.commissioning_date,device.power_kw,
             device.maintenance_interval_months,device.maintenance_next_due_date,
             1 if device.maintenance_customer else 0,
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
            maintenance_next_due_date=?,maintenance_customer=?,online_capable=?,notes=?,updated_at=? WHERE id=?""",
            (device.manufacturer,device.model,device.serial_number,device.touch_id,
             device.construction_year,device.commissioning_date,device.power_kw,
             device.maintenance_interval_months,device.maintenance_next_due_date,
             1 if device.maintenance_customer else 0,
             1 if device.manufacturer.lower() in ("ökofen","oekofen") else 0,
             device.notes,now,device_id))
        if cur.rowcount==0:
            raise HTTPException(404,"Device not found")
    return {"status":"updated","id":device_id}


@app.delete("/api/v1/devices/{device_id}")
def delete_device(device_id: int):
    """Delete one customer device and records/files belonging to that device."""
    file_paths=[]
    with db() as con:
        device=con.execute("""SELECT d.*,i.customer_id FROM devices d
            JOIN installations i ON i.id=d.installation_id WHERE d.id=?""",(device_id,)).fetchone()
        if device is None:
            raise HTTPException(404,"Device not found")
        files=con.execute("SELECT stored_name FROM customer_files WHERE device_id=?",(device_id,)).fetchall()
        file_paths=[FILES_PATH/r["stored_name"] for r in files if r["stored_name"]]

        # Device-bound history is removed with the device. General customer history remains.
        visit_ids=[r["id"] for r in con.execute("SELECT id FROM service_visits WHERE device_id=?",(device_id,))]
        if visit_ids:
            marks=",".join("?" for _ in visit_ids)
            extra=con.execute(f"SELECT stored_name FROM customer_files WHERE visit_id IN ({marks})",visit_ids).fetchall()
            file_paths.extend(FILES_PATH/r["stored_name"] for r in extra if r["stored_name"])
            con.execute(f"DELETE FROM customer_files WHERE visit_id IN ({marks})",visit_ids)
        con.execute("DELETE FROM customer_files WHERE device_id=?",(device_id,))
        con.execute("DELETE FROM customer_credentials WHERE device_id=?",(device_id,))
        con.execute("DELETE FROM tasks WHERE device_id=?",(device_id,))
        con.execute("DELETE FROM maintenance_jobs WHERE device_id=?",(device_id,))
        con.execute("DELETE FROM service_visits WHERE device_id=?",(device_id,))
        con.execute("DELETE FROM devices WHERE id=?",(device_id,))

        # Remove the automatically-created installation shell when it no longer contains devices.
        remaining=con.execute("SELECT 1 FROM devices WHERE installation_id=? LIMIT 1",(device["installation_id"],)).fetchone()
        if remaining is None:
            con.execute("DELETE FROM installations WHERE id=?",(device["installation_id"],))

    for path in set(file_paths):
        try:
            if path.exists(): path.unlink()
        except OSError:
            pass
    return {"status":"deleted","id":device_id}


class DeviceMaintenanceCustomer(BaseModel):
    maintenance_customer: bool


@app.put("/api/v1/devices/{device_id}/maintenance-customer")
def update_device_maintenance_customer(device_id: int, item: DeviceMaintenanceCustomer):
    with db() as con:
        cur=con.execute("""UPDATE devices SET maintenance_customer=?,updated_at=? WHERE id=?""",
            (1 if item.maintenance_customer else 0,datetime.now(timezone.utc).isoformat(),device_id))
        if cur.rowcount==0:
            raise HTTPException(404,"Device not found")
        row=con.execute("SELECT maintenance_customer FROM devices WHERE id=?",(device_id,)).fetchone()
    return {"status":"updated","id":device_id,"maintenance_customer":bool(row["maintenance_customer"])}


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
    device_id: int | None = None
    category: str = "Notiz"
    entry_type: str = "note"
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
    device_id: int | None = Form(default=None),
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
            (customer_id,visit_id,maintenance_id,device_id,file_name,stored_name,content_type,description,created_at,file_category)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (customer_id,visit_id,maintenance_id,device_id,file.filename or stored_name,stored_name,
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


@app.put("/api/v1/customers/{customer_id}/history/{entry_id}")
def update_history_entry(customer_id: int, entry_id: int, item: ServiceVisitCreate):
    with db() as con:
        cur=con.execute("""UPDATE service_visits
            SET visit_date=?,title=?,description=?,device_id=?,category=?,entry_type=?,updated_at=?
            WHERE id=? AND customer_id=?""",
            (item.visit_date,item.title,item.description,item.device_id,item.category,item.entry_type,
             datetime.now(timezone.utc).isoformat(),entry_id,customer_id))
        if cur.rowcount==0:
            raise HTTPException(404,"History entry not found")
    return {"status":"updated","id":entry_id}


@app.delete("/api/v1/customers/{customer_id}/history/{entry_id}")
def delete_history_entry(customer_id: int, entry_id: int):
    with db() as con:
        row=con.execute("SELECT id FROM service_visits WHERE id=? AND customer_id=?",(entry_id,customer_id)).fetchone()
        if row is None:
            raise HTTPException(404,"History entry not found")
        files=con.execute("SELECT stored_name FROM customer_files WHERE visit_id=?",(entry_id,)).fetchall()
        con.execute("DELETE FROM customer_files WHERE visit_id=?",(entry_id,))
        con.execute("DELETE FROM service_visits WHERE id=? AND customer_id=?",(entry_id,customer_id))
    for f in files:
        try:
            path=FILES_PATH/f["stored_name"]
            if path.exists(): path.unlink()
        except Exception:
            pass
    return {"status":"deleted","id":entry_id}


@app.post("/api/v1/customers/{customer_id}/visits")
def create_customer_visit(customer_id: int, visit: ServiceVisitCreate):
    now=datetime.now(timezone.utc).isoformat()
    with db() as con:
        if con.execute("SELECT 1 FROM customers WHERE id=?",(customer_id,)).fetchone() is None:
            raise HTTPException(404,"Customer not found")
        cur=con.execute("""INSERT INTO service_visits
            (customer_id,visit_date,title,description,duration_hours,travel_km,material,
             invoice_reference,device_id,category,entry_type,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'completed',?,?)""",
            (customer_id,visit.visit_date,visit.title,visit.description,visit.duration_hours,
             visit.travel_km,visit.material,visit.invoice_reference,visit.device_id,visit.category,visit.entry_type,now,now))
    return {"status":"created","id":cur.lastrowid}


@app.get("/api/v1/customers/{customer_id}/history")
def list_customer_history(customer_id: int):
    with db() as con:
        rows=con.execute("""SELECT v.*,d.manufacturer device_manufacturer,d.model device_model
            FROM service_visits v
            LEFT JOIN devices d ON d.id=v.device_id
            WHERE v.customer_id=?
            ORDER BY v.visit_date DESC,v.id DESC""",(customer_id,)).fetchall()
    return {"history":[dict(r) for r in rows]}


class CustomerCredentialWrite(BaseModel):
    credential_type: str = "Sonstiges"
    title: str
    device_id: int | None = None
    url: str | None = None
    username: str | None = None
    secret: str | None = None
    notes: str | None = None

def credential_public(row, reveal=False):
    item=dict(row); secret=item.pop("secret_encrypted",None); notes=item.pop("notes_encrypted",None)
    item["has_secret"]=bool(secret); item["secret"]=decrypt_credential(secret) if reveal else None
    item["notes"]=decrypt_credential(notes)
    return item

@app.get("/api/v1/customers/{customer_id}/credentials")
def list_customer_credentials(customer_id: int):
    with db() as con:
        rows=con.execute("""SELECT cc.*,d.manufacturer device_manufacturer,d.model device_model FROM customer_credentials cc
            LEFT JOIN devices d ON d.id=cc.device_id WHERE cc.customer_id=? ORDER BY cc.title COLLATE NOCASE""",(customer_id,)).fetchall()
    return {"credentials":[credential_public(r) for r in rows]}

@app.get("/api/v1/customers/{customer_id}/credentials/{credential_id}")
def reveal_customer_credential(customer_id: int, credential_id: int):
    with db() as con: row=con.execute("SELECT * FROM customer_credentials WHERE id=? AND customer_id=?",(credential_id,customer_id)).fetchone()
    if row is None: raise HTTPException(404,"Credential not found")
    return credential_public(row,True)

@app.post("/api/v1/customers/{customer_id}/credentials")
def create_customer_credential(customer_id: int, item: CustomerCredentialWrite):
    title=item.title.strip()
    if not title: raise HTTPException(400,"Title is required")
    now=datetime.now(timezone.utc).isoformat()
    with db() as con:
        if con.execute("SELECT 1 FROM customers WHERE id=?",(customer_id,)).fetchone() is None: raise HTTPException(404,"Customer not found")
        cur=con.execute("""INSERT INTO customer_credentials (customer_id,device_id,credential_type,title,url,username,secret_encrypted,notes_encrypted,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",(customer_id,item.device_id,item.credential_type,title,item.url,item.username,encrypt_credential(item.secret),encrypt_credential(item.notes),now,now))
    return {"status":"created","id":cur.lastrowid}

@app.put("/api/v1/customers/{customer_id}/credentials/{credential_id}")
def update_customer_credential(customer_id: int, credential_id: int, item: CustomerCredentialWrite):
    title=item.title.strip()
    if not title: raise HTTPException(400,"Title is required")
    with db() as con:
        old=con.execute("SELECT * FROM customer_credentials WHERE id=? AND customer_id=?",(credential_id,customer_id)).fetchone()
        if old is None: raise HTTPException(404,"Credential not found")
        secret=old["secret_encrypted"] if item.secret is None else encrypt_credential(item.secret)
        notes=encrypt_credential(item.notes)
        con.execute("""UPDATE customer_credentials SET device_id=?,credential_type=?,title=?,url=?,username=?,secret_encrypted=?,notes_encrypted=?,updated_at=? WHERE id=? AND customer_id=?""",
            (item.device_id,item.credential_type,title,item.url,item.username,secret,notes,datetime.now(timezone.utc).isoformat(),credential_id,customer_id))
    return {"status":"updated","id":credential_id}

@app.delete("/api/v1/customers/{customer_id}/credentials/{credential_id}")
def delete_customer_credential(customer_id: int, credential_id: int):
    with db() as con:
        cur=con.execute("DELETE FROM customer_credentials WHERE id=? AND customer_id=?",(credential_id,customer_id))
        if cur.rowcount==0: raise HTTPException(404,"Credential not found")
    return {"status":"deleted","id":credential_id}

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


class PortalLogin(BaseModel):
    username: str
    password: str


class PortalUserCreate(BaseModel):
    username: str
    display_name: str
    password: str
    is_admin: bool = False
    role: str = "end_customer"
    access_status: str = "active"
    trial_start: str | None = None
    trial_end: str | None = None
    custom_dashboard_id: int | None = None
    dashboard_ids: list[str] = []


class PortalUserUpdate(BaseModel):
    display_name: str
    active: bool = True
    is_admin: bool = False
    role: str = "end_customer"
    access_status: str = "active"
    trial_start: str | None = None
    trial_end: str | None = None
    custom_dashboard_id: int | None = None
    password: str | None = None
    dashboard_ids: list[str] = []


def _portal_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def _portal_session(token: str | None) -> dict[str, Any]:
    if not token:
        raise HTTPException(401, "PORTAL_LOGIN_REQUIRED")
    with db() as con:
        row = con.execute("""SELECT s.token,u.id,u.username,u.display_name,u.is_admin
            FROM portal_sessions s JOIN portal_users u ON u.id=s.user_id
            WHERE s.token=? AND s.expires_at>?""", (token, datetime.now(timezone.utc).isoformat())).fetchone()
    if row is None:
        raise HTTPException(401, "PORTAL_SESSION_INVALID")
    return dict(row)


def _portal_bootstrap(user: dict[str, Any]) -> dict[str, Any]:
    with db() as con:
        rows = con.execute("""SELECT d.id,d.name,d.description,d.module,d.config_json
            FROM portal_dashboards d JOIN portal_user_dashboards ud ON ud.dashboard_id=d.id
            WHERE ud.user_id=? ORDER BY d.sort_order,d.name""", (user["id"],)).fetchall()
    dashboards = [{**dict(r), "config": json.loads(r["config_json"] or "{}")} for r in rows]
    for d in dashboards:
        d.pop("config_json", None)
    return {
        "display_name": user["display_name"],
        "is_admin": bool(user["is_admin"]),
        "status": "Pilot" if not user["is_admin"] else "Administrator",
        "message": "Kundenportal aktiv.",
        "dashboards": dashboards,
        "systems": [
            {"id": d["id"], "name": d["name"], "description": d["description"], "status": "Bereit"}
            for d in dashboards
        ],
    }


def _portal_admin(ins_portal_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = _portal_session(ins_portal_session)
    if not user["is_admin"]:
        raise HTTPException(403, "PORTAL_ADMIN_REQUIRED")
    return user


@app.get("/portal/api/admin/users")
def portal_admin_users(ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session)
    with db() as con:
        dashboards = [dict(x) for x in con.execute("SELECT id,name,description FROM portal_dashboards ORDER BY sort_order,name")]
        users = []
        for row in con.execute("SELECT id,username,display_name,is_admin,role,access_status,trial_start,trial_end,custom_dashboard_id,active,created_at FROM portal_users ORDER BY display_name"):
            item = dict(row)
            item["dashboard_ids"] = [x["dashboard_id"] for x in con.execute("SELECT dashboard_id FROM portal_user_dashboards WHERE user_id=?",(row["id"],))]
            users.append(item)
    return {"users": users, "dashboards": dashboards}


@app.post("/portal/api/admin/users")
def portal_admin_create_user(request: PortalUserCreate, ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session)
    username = request.username.strip().lower()
    if not username or len(request.password) < 8:
        raise HTTPException(400, "PORTAL_USER_INVALID")
    salt = secrets.token_hex(16)
    try:
        with db() as con:
            role=request.role if request.role in ("admin","partner","end_customer") else "end_customer"
            status=request.access_status if request.access_status in ("active","trial","suspended") else "active"
            cur = con.execute("""INSERT INTO portal_users(username,display_name,password_salt,password_hash,is_admin,role,access_status,trial_start,trial_end,custom_dashboard_id,active,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,1,?)""",(username,request.display_name.strip(),salt,_portal_hash(request.password,salt),int(role=="admin"),role,status,request.trial_start,request.trial_end,request.custom_dashboard_id,datetime.now(timezone.utc).isoformat()))
            uid = cur.lastrowid
            allowed={x["id"] for x in con.execute("SELECT id FROM portal_dashboards")}
            ids = allowed if request.is_admin else set(request.dashboard_ids) & allowed
            for did in ids:
                con.execute("INSERT INTO portal_user_dashboards(user_id,dashboard_id) VALUES(?,?)",(uid,did))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "PORTAL_USERNAME_EXISTS")
    return {"created": True, "id": uid}


@app.put("/portal/api/admin/users/{user_id}")
def portal_admin_update_user(user_id: int, request: PortalUserUpdate, ins_portal_session: str | None = Cookie(default=None)):
    admin = _portal_admin(ins_portal_session)
    with db() as con:
        row=con.execute("SELECT * FROM portal_users WHERE id=?",(user_id,)).fetchone()
        if row is None: raise HTTPException(404, "PORTAL_USER_NOT_FOUND")
        if user_id == admin["id"] and not request.active:
            raise HTTPException(400, "PORTAL_CANNOT_DISABLE_SELF")
        role=request.role if request.role in ("admin","partner","end_customer") else "end_customer"
        status=request.access_status if request.access_status in ("active","trial","suspended") else "active"
        con.execute("UPDATE portal_users SET display_name=?,active=?,is_admin=?,role=?,access_status=?,trial_start=?,trial_end=?,custom_dashboard_id=? WHERE id=?",(request.display_name.strip(),int(request.active),int(role=="admin"),role,status,request.trial_start,request.trial_end,request.custom_dashboard_id,user_id))
        if request.password:
            if len(request.password) < 8: raise HTTPException(400, "PORTAL_PASSWORD_TOO_SHORT")
            salt=secrets.token_hex(16)
            con.execute("UPDATE portal_users SET password_salt=?,password_hash=? WHERE id=?",(salt,_portal_hash(request.password,salt),user_id))
            con.execute("DELETE FROM portal_sessions WHERE user_id=?",(user_id,))
        con.execute("DELETE FROM portal_user_dashboards WHERE user_id=?",(user_id,))
        allowed={x["id"] for x in con.execute("SELECT id FROM portal_dashboards")}
        ids = allowed if request.is_admin else set(request.dashboard_ids) & allowed
        for did in ids: con.execute("INSERT INTO portal_user_dashboards(user_id,dashboard_id) VALUES(?,?)",(user_id,did))
    return {"updated": True}


@app.delete("/portal/api/admin/users/{user_id}")
def portal_admin_delete_user(user_id: int, ins_portal_session: str | None = Cookie(default=None)):
    admin = _portal_admin(ins_portal_session)
    if user_id == admin["id"]: raise HTTPException(400, "PORTAL_CANNOT_DELETE_SELF")
    with db() as con:
        con.execute("DELETE FROM portal_sessions WHERE user_id=?",(user_id,))
        con.execute("DELETE FROM portal_user_dashboards WHERE user_id=?",(user_id,))
        cur=con.execute("DELETE FROM portal_users WHERE id=?",(user_id,))
        if not cur.rowcount: raise HTTPException(404, "PORTAL_USER_NOT_FOUND")
    return {"deleted": True}


@app.post("/portal/api/login")
def customer_portal_login(request: PortalLogin, response: Response):
    with db() as con:
        row = con.execute("SELECT * FROM portal_users WHERE lower(username)=lower(?) AND active=1", (request.username.strip(),)).fetchone()
        if row is None or not hmac.compare_digest(_portal_hash(request.password, row["password_salt"]), row["password_hash"]):
            raise HTTPException(401, "PORTAL_LOGIN_FAILED")
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        con.execute("INSERT INTO portal_sessions(token,user_id,expires_at) VALUES(?,?,?)", (token,row["id"],expires))
    response.set_cookie("ins_portal_session", token, max_age=30*86400, httponly=True, secure=True, samesite="lax")
    return {"logged_in": True, "display_name": row["display_name"]}


@app.post("/portal/api/logout")
def customer_portal_logout(response: Response, ins_portal_session: str | None = Cookie(default=None)):
    if ins_portal_session:
        with db() as con:
            con.execute("DELETE FROM portal_sessions WHERE token=?", (ins_portal_session,))
    response.delete_cookie("ins_portal_session")
    return {"logged_in": False}


class OschmalzHeatingCommand(BaseModel):
    mode: str | None = None
    comfort_temperature: float | None = None
    eco_temperature: float | None = None
    bedroom: bool | None = None
    bathroom: bool | None = None


@app.post("/portal/api/oschmalz/heating/command")
def customer_portal_oschmalz_command(request: OschmalzHeatingCommand, ins_portal_session: str | None = Cookie(default=None)):
    user = _portal_session(ins_portal_session)
    with db() as con:
        allowed = con.execute("""SELECT 1 FROM portal_user_dashboards
            WHERE user_id=? AND dashboard_id='oschmalz-heating'""",(user["id"],)).fetchone()
    if allowed is None and not user["is_admin"]:
        raise HTTPException(403, "PORTAL_DASHBOARD_FORBIDDEN")
    payload = {}
    if request.mode is not None:
        if request.mode not in {"AUS","HEIZEN","ECO"}: raise HTTPException(400,"PORTAL_MODE_INVALID")
        payload["mode"] = request.mode
    if request.comfort_temperature is not None:
        if not 15 <= request.comfort_temperature <= 25: raise HTTPException(400,"PORTAL_COMFORT_INVALID")
        payload["comfort_temperature"] = request.comfort_temperature
    if request.eco_temperature is not None:
        if not 10 <= request.eco_temperature <= 22: raise HTTPException(400,"PORTAL_ECO_INVALID")
        payload["eco_temperature"] = request.eco_temperature
    if request.bedroom is not None: payload["bedroom"] = request.bedroom
    if request.bathroom is not None: payload["bathroom"] = request.bathroom
    if not payload: raise HTTPException(400,"PORTAL_COMMAND_EMPTY")
    payload["requested_at"] = datetime.now(timezone.utc).isoformat()
    payload["requested_by"] = user["username"]
    with MQTT_SERVICE_LOCK:
        client = MQTT_SERVICE_CLIENT
    if client is None or not client.is_connected():
        raise HTTPException(503,"MQTT_NOT_CONNECTED")
    info=client.publish("ins-ei/oschmalz/command",json.dumps(payload,ensure_ascii=False),qos=1,retain=False)
    if info.rc != mqtt.MQTT_ERR_SUCCESS: raise HTTPException(503,"MQTT_PUBLISH_FAILED")
    return {"accepted":True,"command":payload}


@app.get("/portal/api/oschmalz/heating")
def customer_portal_oschmalz_heating(ins_portal_session: str | None = Cookie(default=None)):
    user = _portal_session(ins_portal_session)
    with db() as con:
        allowed = con.execute("""SELECT 1 FROM portal_user_dashboards
            WHERE user_id=? AND dashboard_id='oschmalz-heating'""",(user["id"],)).fetchone()
    if allowed is None and not user["is_admin"]:
        raise HTTPException(403, "PORTAL_DASHBOARD_FORBIDDEN")
    with MQTT_LIVE_LOCK:
        item = dict(MQTT_LIVE.get("oschmalz") or {})
        values = dict(item.get("values") or {})
    received = item.get("received_at")
    age = None
    if received:
        try:
            age = max(0, int((datetime.now(timezone.utc)-datetime.fromisoformat(received)).total_seconds()))
        except ValueError:
            pass
    return {"online": age is not None and age < 600, "age_seconds": age, "values": values}


def _portal_oekofen_plant(user: dict[str,Any], plant_id: str | None=None):
    with db() as con:
        if user["is_admin"] and plant_id:
            row=con.execute("""SELECT p.*,c.id customer_id,c.name customer_name FROM oekofen_plants p
                LEFT JOIN devices d ON d.oekofen_plant_id=p.plant_id
                LEFT JOIN installations i ON i.id=d.installation_id LEFT JOIN customers c ON c.id=i.customer_id
                WHERE p.plant_id=?""",(plant_id,)).fetchone()
        else:
            row=con.execute("""SELECT p.*,c.id customer_id,c.name customer_name FROM oekofen_plants p
                JOIN devices d ON d.oekofen_plant_id=p.plant_id JOIN installations i ON i.id=d.installation_id
                JOIN customers c ON c.id=i.customer_id JOIN portal_users u ON lower(u.display_name)=lower(c.name)
                WHERE u.id=? ORDER BY p.plant_name LIMIT 1""",(user["id"],)).fetchone()
    if row is None: raise HTTPException(404,"PORTAL_OEKOFEN_NOT_FOUND")
    return dict(row)

def _oekofen_portal_normalize(label: str, value: str | None, *, target: bool=False) -> str | None:
    """Normalize manufacturer sentinel values for customer-facing OekoFEN views."""
    if value is None:return None
    text=str(value).strip()
    if not text:return None
    # Pelletronic uses -3276.8 C as an invalid/not-present temperature sentinel.
    try:
        number=float(text.split()[0].replace(",","."))
    except (ValueError,IndexError):
        return text
    if number <= -3000:return None
    # For temperature targets, 8 C means there is currently no heat demand.
    if target and "°C" in text and abs(number-8.0)<0.01:return None
    # Room temperature 0 C represents a missing/inactive room sensor in these variables.
    if "Raumtemperatur" in label and abs(number)<0.01:return None
    return text

def _oekofen_portal_live(plant_id: str) -> dict[str,Any]:
    names=[]
    for _,actual,target in OEKOFEN_MEASUREMENTS:
        names.append(actual)
        if target:names.append(target)
    values=oekofen_fetch_variables(plant_id,list(dict.fromkeys(names)))
    by_name={x.get("name"):x for x in values if isinstance(x,dict)}
    rows=[]
    for label,actual,target in OEKOFEN_MEASUREMENTS:
        av=_oekofen_portal_normalize(label,oekofen_format_variable(by_name.get(actual)))
        tv=_oekofen_portal_normalize(label,oekofen_format_variable(by_name.get(target)),target=True) if target else None
        if av is not None or tv is not None:rows.append({"label":label,"actual":av,"target":tv})
    return {"rows":rows,"updated_at":datetime.now(timezone.utc).isoformat()}

class PortalDashboardConfigSave(BaseModel):
    name: str
    active: bool = True
    description: str | None = None
    access: list[dict[str,Any]] = []
    sources: list[dict[str,Any]] = []
    config: dict[str,Any] = {}

@app.get("/portal/api/admin/dashboard-configs")
def portal_admin_dashboard_configs(ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session)
    with db() as con:
        rows=con.execute("SELECT * FROM portal_dashboard_configs ORDER BY name COLLATE NOCASE,id").fetchall()
        result=[]
        for r in rows:
            x=dict(r);x["config"]=json.loads(x.pop("config_json") or "{}")
            x["access"]=[dict(a) for a in con.execute("SELECT user_id,permission FROM portal_dashboard_access WHERE dashboard_config_id=? ORDER BY user_id",(x["id"],))]
            x["sources"]=[]
            for src in con.execute("SELECT id,source_type,name,source_ref,config_json FROM portal_dashboard_sources WHERE dashboard_config_id=? ORDER BY id",(x["id"],)):
                y=dict(src);y["config"]=json.loads(y.pop("config_json") or "{}");x["sources"].append(y)
            result.append(x)
    return {"dashboards":result}

@app.post("/portal/api/admin/dashboard-configs")
def portal_admin_dashboard_config_create(item: PortalDashboardConfigSave, ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session);now=datetime.now(timezone.utc).isoformat()
    payload={"description":item.description or "","blocks":item.config.get("blocks",[])}
    with db() as con:
        cur=con.execute("INSERT INTO portal_dashboard_configs(name,active,config_json,created_at,updated_at) VALUES(?,?,?,?,?)",(item.name.strip(),int(item.active),json.dumps(payload,ensure_ascii=False),now,now));did=cur.lastrowid
        for x in item.access:
            if x.get("user_id") and x.get("permission") in ("read","write"):con.execute("INSERT OR REPLACE INTO portal_dashboard_access VALUES(?,?,?)",(did,int(x["user_id"]),x["permission"]))
        for x in item.sources:
            if x.get("type") and x.get("ref"):con.execute("INSERT INTO portal_dashboard_sources(dashboard_config_id,source_type,name,source_ref,config_json) VALUES(?,?,?,?,?)",(did,x["type"],x.get("name") or x["type"],str(x["ref"]),json.dumps(x.get("config") or {},ensure_ascii=False)))
    return {"id":did,"created":True}

class PortalDashboardTemplateSave(BaseModel):
    name: str
    block_type: str
    source_type: str | None = None
    template: dict[str,Any]

@app.get("/portal/api/admin/dashboard-templates")
def portal_admin_dashboard_templates(ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session)
    with db() as con:rows=con.execute("SELECT * FROM portal_dashboard_templates ORDER BY name COLLATE NOCASE").fetchall()
    result=[]
    for r in rows:
        x=dict(r);x["template"]=json.loads(x.pop("template_json") or "{}");result.append(x)
    return {"templates":result}

@app.post("/portal/api/admin/dashboard-templates")
def portal_admin_dashboard_template_create(item: PortalDashboardTemplateSave,ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session)
    if item.block_type not in ("card","chart","combo"):raise HTTPException(400,"INVALID_TEMPLATE_TYPE")
    now=datetime.now(timezone.utc).isoformat()
    with db() as con:
        cur=con.execute("INSERT INTO portal_dashboard_templates(name,block_type,source_type,template_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (item.name.strip(),item.block_type,item.source_type,json.dumps(item.template,ensure_ascii=False),now,now))
    return {"id":cur.lastrowid,"created":True}

@app.delete("/portal/api/admin/dashboard-templates/{template_id}")
def portal_admin_dashboard_template_delete(template_id:int,ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session)
    with db() as con:
        cur=con.execute("DELETE FROM portal_dashboard_templates WHERE id=?",(template_id,))
        if not cur.rowcount:raise HTTPException(404,"TEMPLATE_NOT_FOUND")
    return {"deleted":True}

@app.put("/portal/api/admin/dashboard-configs/{config_id}")
def portal_admin_dashboard_config_update(config_id:int,item:PortalDashboardConfigSave,ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session);now=datetime.now(timezone.utc).isoformat();payload={"description":item.description or "","blocks":item.config.get("blocks",[])}
    with db() as con:
        cur=con.execute("UPDATE portal_dashboard_configs SET name=?,active=?,config_json=?,updated_at=? WHERE id=?",(item.name.strip(),int(item.active),json.dumps(payload,ensure_ascii=False),now,config_id))
        if not cur.rowcount:raise HTTPException(404,"DASHBOARD_CONFIG_NOT_FOUND")
        con.execute("DELETE FROM portal_dashboard_access WHERE dashboard_config_id=?",(config_id,));con.execute("DELETE FROM portal_dashboard_sources WHERE dashboard_config_id=?",(config_id,))
        for x in item.access:
            if x.get("user_id") and x.get("permission") in ("read","write"):con.execute("INSERT INTO portal_dashboard_access VALUES(?,?,?)",(config_id,int(x["user_id"]),x["permission"]))
        for x in item.sources:
            if x.get("type") and x.get("ref"):con.execute("INSERT INTO portal_dashboard_sources(dashboard_config_id,source_type,name,source_ref,config_json) VALUES(?,?,?,?,?)",(config_id,x["type"],x.get("name") or x["type"],str(x["ref"]),json.dumps(x.get("config") or {},ensure_ascii=False)))
    return {"updated":True}

@app.delete("/portal/api/admin/dashboard-configs/{config_id}")
def portal_admin_dashboard_config_delete(config_id:int,ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session)
    with db() as con:
        con.execute("DELETE FROM portal_dashboard_access WHERE dashboard_config_id=?",(config_id,));con.execute("DELETE FROM portal_dashboard_sources WHERE dashboard_config_id=?",(config_id,))
        cur=con.execute("DELETE FROM portal_dashboard_configs WHERE id=?",(config_id,))
        if not cur.rowcount:raise HTTPException(404,"DASHBOARD_CONFIG_NOT_FOUND")
    return {"deleted":True}

@app.get("/portal/api/admin/dashboard-builder-data")
def portal_admin_dashboard_builder_data(ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session)
    with db() as con:
        users=[dict(r) for r in con.execute("SELECT id,username,display_name,is_admin FROM portal_users WHERE active=1 ORDER BY display_name")]
        plants=[dict(r) for r in con.execute("SELECT plant_id,plant_name,serial_number FROM oekofen_plants ORDER BY plant_name COLLATE NOCASE")]
    return {"users":users,"source_types":[{"id":"oekofen","name":"ÖkoFEN"},{"id":"mypv","name":"my-PV"},{"id":"goodwe","name":"GoodWe"},{"id":"mqtt","name":"MQTT / INS-EI"},{"id":"home_assistant","name":"Home Assistant"}],"oekofen_plants":plants}

@app.get("/portal/api/admin/source-history")
def portal_admin_source_history(source_type:str,source_ref:str,keys:str,period:str="24h",ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session)
    if source_type!="oekofen":return {"series":{}}
    ranges={"24h":"-24h","7d":"-7d","30d":"-30d"};windows={"24h":"10m","7d":"1h","30d":"4h"}
    if period not in ranges:raise HTTPException(400,"INVALID_PERIOD")
    wanted=[x for x in keys.split(",") if x][:20]
    if not wanted:return {"series":{}}
    token=INFLUX_TOKEN.read_text().strip();safe=source_ref.replace('"','\\\"')
    fields=" or ".join(['r._field=="'+x.replace('"','\\\"')+'"' for x in wanted])
    flux='from(bucket: "'+INFLUX_BUCKET+'") |> range(start:'+ranges[period]+') |> filter(fn:(r)=>r._measurement=="oekofen_csv" and r.plant_id=="'+safe+'") |> filter(fn:(r)=>'+fields+') |> filter(fn:(r)=>r._value > -3000.0) |> aggregateWindow(every:'+windows[period]+',fn:mean,createEmpty:false) |> keep(columns:["_time","_field","_value"])'
    req=Request(INFLUX_URL+"/api/v2/query?org="+INFLUX_ORG,data=json.dumps({"query":flux,"type":"flux"}).encode(),method="POST",headers={"Authorization":f"Token {token}","Content-Type":"application/json","Accept":"application/csv"})
    raw=urlopen(req,timeout=20).read().decode();series={}
    for row in csv.DictReader(io.StringIO(raw)):
        try:v=float(row.get("_value",""))
        except ValueError:continue
        series.setdefault(row.get("_field",""),[]).append({"time":row.get("_time"),"value":v})
    return {"series":series}

@app.get("/portal/api/admin/source-samples")
def portal_admin_source_samples(source_type: str, source_ref: str, ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session)
    if source_type!="oekofen":return {"source_type":source_type,"source_ref":source_ref,"signals":[],"note":"Connector noch nicht implementiert"}
    signals=[];names=[]
    for label,actual,target in OEKOFEN_MEASUREMENTS:
        names.append(actual)
        if target:names.append(target)
    try:
        values=oekofen_fetch_variables(source_ref,list(dict.fromkeys(names)));by={x.get("name"):x for x in values if isinstance(x,dict)}
        for label,actual,target in OEKOFEN_MEASUREMENTS:
            for key,role in ((actual,"actual"),(target,"target")):
                if key and by.get(key):
                    item=by[key];signals.append({"scope":"live","key":key,"label":label+(" Soll" if role=="target" else ""),"sample":oekofen_format_variable(item),"raw":item.get("value"),"unit":item.get("unitText") or ""})
    except Exception:pass
    token=INFLUX_TOKEN.read_text().strip();safe=source_ref.replace('"','\\\"')
    flux='from(bucket: "'+INFLUX_BUCKET+'") |> range(start:-7d) |> filter(fn:(r)=>r._measurement=="oekofen_csv" and r.plant_id=="'+safe+'") |> group(columns:["_field"]) |> last() |> keep(columns:["_time","_field","_value"])'
    try:
        req=Request(INFLUX_URL+"/api/v2/query?org="+INFLUX_ORG,data=json.dumps({"query":flux,"type":"flux"}).encode(),method="POST",headers={"Authorization":f"Token {token}","Content-Type":"application/json","Accept":"application/csv"});raw=urlopen(req,timeout=20).read().decode()
        for r in csv.DictReader(io.StringIO(raw)):
            if r.get("_field"):signals.append({"scope":"history","key":r["_field"],"label":r["_field"],"sample":r.get("_value")})
    except Exception:pass
    return {"source_type":"oekofen","source_ref":source_ref,"signals":signals}

@app.get("/portal/api/admin/oekofen/{plant_id}/available-signals")
def portal_admin_oekofen_signals(plant_id: str, ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session)
    live=[]
    try:
        names=[]
        for label,actual,target in OEKOFEN_MEASUREMENTS:
            names.append(actual)
            if target:names.append(target)
        values=oekofen_fetch_variables(plant_id,list(dict.fromkeys(names)))
        by={x.get("name"):x for x in values if isinstance(x,dict)}
        for label,actual,target in OEKOFEN_MEASUREMENTS:
            if by.get(actual):live.append({"source":"oekofen_live","key":actual,"label":label,"role":"actual"})
            if target and by.get(target):live.append({"source":"oekofen_live","key":target,"label":label+" Soll","role":"target"})
    except Exception:
        pass
    fields=[]
    token=INFLUX_TOKEN.read_text().strip();safe=plant_id.replace('"','\\\"')
    flux=f'''import "influxdata/influxdb/schema"
schema.measurementFieldKeys(bucket: "{INFLUX_BUCKET}", measurement: "oekofen_csv", start: -30d)
 |> yield(name: "fields")'''
    try:
        req=Request(INFLUX_URL+"/api/v2/query?org="+INFLUX_ORG,data=json.dumps({"query":flux,"type":"flux"}).encode(),method="POST",headers={"Authorization":f"Token {token}","Content-Type":"application/json","Accept":"application/csv"})
        raw=urlopen(req,timeout=20).read().decode()
        fields=[r.get("_value") for r in csv.DictReader(io.StringIO(raw)) if r.get("_value")]
    except Exception:pass
    return {"plant_id":plant_id,"live":live,"history":[{"source":"influx","key":x,"label":x} for x in fields]}


@app.get("/portal/api/oekofen/plants")
def portal_oekofen_plants(ins_portal_session: str | None = Cookie(default=None)):
    user=_portal_session(ins_portal_session)
    if not user["is_admin"]: raise HTTPException(403,"PORTAL_ADMIN_REQUIRED")
    with db() as con:
        rows=con.execute("""SELECT p.plant_id,p.plant_name,p.serial_number,c.name customer_name,c.city customer_city
            FROM oekofen_plants p
            LEFT JOIN devices d ON d.oekofen_plant_id=p.plant_id
            LEFT JOIN installations i ON i.id=d.installation_id
            LEFT JOIN customers c ON c.id=i.customer_id
            ORDER BY p.plant_name COLLATE NOCASE""").fetchall()
    return {"plants":[dict(r) for r in rows]}

@app.get("/portal/api/oekofen")
def portal_oekofen(ins_portal_session: str | None = Cookie(default=None), plant_id: str | None=None):
    user=_portal_session(ins_portal_session);plant=_portal_oekofen_plant(user,plant_id)
    return {"plant":{"plant_id":plant["plant_id"],"name":plant["plant_name"],"serial_number":plant["serial_number"],"customer_name":plant["customer_name"]}}

@app.get("/portal/api/oekofen/live")
def portal_oekofen_live(ins_portal_session: str | None = Cookie(default=None), plant_id: str | None=None):
    user=_portal_session(ins_portal_session);plant=_portal_oekofen_plant(user,plant_id)
    try:return {"plant_id":plant["plant_id"],**_oekofen_portal_live(plant["plant_id"])}
    except Exception as exc:raise HTTPException(502,str(exc))

@app.get("/portal/api/oekofen/history")
def portal_oekofen_history(period: str="24h", ins_portal_session: str | None = Cookie(default=None), plant_id: str | None=None):
    user=_portal_session(ins_portal_session);plant=_portal_oekofen_plant(user,plant_id)
    ranges={"24h":"-24h","7d":"-7d","30d":"-30d"};windows={"24h":"10m","7d":"1h","30d":"4h"}
    if period not in ranges:raise HTTPException(400,"INVALID_PERIOD")
    token=INFLUX_TOKEN.read_text().strip();safe=plant["plant_id"].replace('"','\\\"')
    flux=f'''from(bucket: "{INFLUX_BUCKET}")
 |> range(start: {ranges[period]})
 |> filter(fn:(r)=>r._measurement=="oekofen_csv" and r.plant_id=="{safe}")
 |> filter(fn:(r)=>r._field =~ /AT|Kessel|PE1 KT|PU1|WW1|HK1 VL|HK2 VL/)
 |> filter(fn:(r)=>r._value > -3000.0)
 |> filter(fn:(r)=>not (r._field =~ /Soll/ and r._value == 8.0))
 |> filter(fn:(r)=>not (r._field =~ /Raum/ and r._value == 0.0))
 |> aggregateWindow(every: {windows[period]}, fn: mean, createEmpty: false)
 |> keep(columns:["_time","_field","_value"])'''
    req=Request(INFLUX_URL+"/api/v2/query?org="+INFLUX_ORG,data=json.dumps({"query":flux,"type":"flux"}).encode(),method="POST",headers={"Authorization":f"Token {token}","Content-Type":"application/json","Accept":"application/csv"})
    raw=urlopen(req,timeout=20).read().decode();series={}
    for row in csv.DictReader(io.StringIO(raw)):
        field=row.get("_field");ts=row.get("_time");val=row.get("_value")
        if not field or not ts or val is None:continue
        try:num=round(float(val),2)
        except ValueError:continue
        series.setdefault(field,[]).append({"time":ts,"value":num})
    return {"plant_id":plant["plant_id"],"period":period,"series":series}


@app.get("/portal/api/me")
def customer_portal_me(ins_portal_session: str | None = Cookie(default=None)):
    return _portal_bootstrap(_portal_session(ins_portal_session))


# DEV portal API aliases. These are explicit FastAPI routes so Caddy only has
app.add_api_route("/dev-portal/api/admin/dashboard-configs", portal_admin_dashboard_configs, methods=["GET"])
app.add_api_route("/dev-portal/api/admin/dashboard-builder-data", portal_admin_dashboard_builder_data, methods=["GET"])
app.add_api_route("/dev-portal/api/admin/dashboard-templates", portal_admin_dashboard_templates, methods=["GET"])
app.add_api_route("/dev-portal/api/admin/dashboard-templates", portal_admin_dashboard_template_create, methods=["POST"])
app.add_api_route("/dev-portal/api/admin/dashboard-templates/{template_id}", portal_admin_dashboard_template_delete, methods=["DELETE"])
app.add_api_route("/dev-portal/api/admin/source-samples", portal_admin_source_samples, methods=["GET"])
app.add_api_route("/dev-portal/api/admin/source-history", portal_admin_source_history, methods=["GET"])
app.add_api_route("/dev-portal/api/admin/dashboard-configs", portal_admin_dashboard_config_create, methods=["POST"])
app.add_api_route("/dev-portal/api/admin/dashboard-configs/{config_id}", portal_admin_dashboard_config_update, methods=["PUT"])
app.add_api_route("/dev-portal/api/admin/dashboard-configs/{config_id}", portal_admin_dashboard_config_delete, methods=["DELETE"])
app.add_api_route("/dev-portal/api/admin/oekofen/{plant_id}/available-signals", portal_admin_oekofen_signals, methods=["GET"])
# to proxy /dev-portal* without path rewriting.
app.add_api_route("/dev-portal/api/login", customer_portal_login, methods=["POST"])
app.add_api_route("/dev-portal/api/logout", customer_portal_logout, methods=["POST"])
app.add_api_route("/dev-portal/api/me", customer_portal_me, methods=["GET"])
app.add_api_route("/dev-portal/api/admin/users", portal_admin_users, methods=["GET"])
app.add_api_route("/dev-portal/api/admin/users", portal_admin_create_user, methods=["POST"])
app.add_api_route("/dev-portal/api/admin/users/{user_id}", portal_admin_update_user, methods=["PUT"])
app.add_api_route("/dev-portal/api/admin/users/{user_id}", portal_admin_delete_user, methods=["DELETE"])
app.add_api_route("/dev-portal/api/oschmalz/heating", customer_portal_oschmalz_heating, methods=["GET"])
app.add_api_route("/dev-portal/api/oschmalz/heating/command", customer_portal_oschmalz_command, methods=["POST"])
app.add_api_route("/dev-portal/api/oekofen/plants", portal_oekofen_plants, methods=["GET"])
app.add_api_route("/dev-portal/api/oekofen", portal_oekofen, methods=["GET"])
app.add_api_route("/dev-portal/api/oekofen/live", portal_oekofen_live, methods=["GET"])
app.add_api_route("/dev-portal/api/oekofen/history", portal_oekofen_history, methods=["GET"])


@app.get("/portal")
def customer_portal_index():
    path = CUSTOMER_PORTAL_DIR / "index.html"
    if not path.exists():
        raise HTTPException(404, "CUSTOMER_PORTAL_NOT_DEPLOYED")
    return FileResponse(path)


@app.get("/portal/{asset_name}")
def customer_portal_asset(asset_name: str):
    if asset_name not in {"portal.css", "portal.js"}:
        raise HTTPException(404, "PORTAL_ASSET_NOT_FOUND")
    path = CUSTOMER_PORTAL_DIR / asset_name
    if not path.exists():
        raise HTTPException(404, "CUSTOMER_PORTAL_NOT_DEPLOYED")
    return FileResponse(path)

@app.get("/dev-portal/api/admin/dashboard-configs/{config_id}/preview-data")
def portal_admin_dashboard_preview_data(config_id:int,period:str="24h",ins_portal_session: str | None = Cookie(default=None)):
    _portal_admin(ins_portal_session)
    ranges={"24h":"-24h","7d":"-7d","30d":"-30d"};windows={"24h":"10m","7d":"1h","30d":"4h"}
    if period not in ranges:raise HTTPException(400,"INVALID_PERIOD")
    with db() as con:
        row=con.execute("SELECT * FROM portal_dashboard_configs WHERE id=?",(config_id,)).fetchone()
        if row is None:raise HTTPException(404,"DASHBOARD_CONFIG_NOT_FOUND")
        cfg=json.loads(row["config_json"] or "{}")
        sources=[dict(x) for x in con.execute("SELECT id,source_type,name,source_ref,config_json FROM portal_dashboard_sources WHERE dashboard_config_id=? ORDER BY id",(config_id,))]
    live={};history={}
    for si,src in enumerate(sources):
        needed=[]
        for b in cfg.get("blocks",[]):
            for it in b.get("items",[]):
                if int(it.get("source_index",-1))==si and it.get("key"):needed.append(it["key"])
        if src["source_type"]!="oekofen" or not needed:continue
        try:
            vals=oekofen_fetch_variables(src["source_ref"],list(dict.fromkeys(needed)))
            for x in vals:
                if x.get("name") in needed:live[f"{si}:{x['name']}"]=oekofen_format_variable(x)
        except Exception:pass
        token=INFLUX_TOKEN.read_text().strip();safe=src["source_ref"].replace('"','\\\"')
        displays={}
        for b in cfg.get("blocks",[]):
            for it in b.get("items",[]):
                if int(it.get("source_index",-1))==si and it.get("key"):displays[it["key"]]=it.get("display","line")
        analog=[k for k in set(needed) if displays.get(k) not in ("binary","percent_binary","mixer")]
        discrete=[k for k in set(needed) if displays.get(k) in ("binary","percent_binary","mixer")]
        queries=[]
        if analog:
            fields=" or ".join(['r._field=="'+k.replace('"','\\\"')+'"' for k in analog])
            base='from(bucket: "'+INFLUX_BUCKET+'") |> range(start:'+ranges[period]+') |> filter(fn:(r)=>r._measurement=="oekofen_csv" and r.plant_id=="'+safe+'") |> filter(fn:(r)=>'+fields+') |> filter(fn:(r)=>r._value > -3000.0)'
            if period=="30d": base+=' |> aggregateWindow(every:'+windows[period]+',fn:mean,createEmpty:false)'
            queries.append(base+' |> keep(columns:["_time","_field","_value"])')
        if discrete:
            fields=" or ".join(['r._field=="'+k.replace('"','\\\"')+'"' for k in discrete])
            base='from(bucket: "'+INFLUX_BUCKET+'") |> range(start:'+ranges[period]+') |> filter(fn:(r)=>r._measurement=="oekofen_csv" and r.plant_id=="'+safe+'") |> filter(fn:(r)=>'+fields+')'
            if period=="30d": base+=' |> aggregateWindow(every:'+windows[period]+',fn:last,createEmpty:false)'
            queries.append(base+' |> keep(columns:["_time","_field","_value"])')
        for flux in queries:
            try:
                req=Request(INFLUX_URL+"/api/v2/query?org="+INFLUX_ORG,data=json.dumps({"query":flux,"type":"flux"}).encode(),method="POST",headers={"Authorization":f"Token {token}","Content-Type":"application/json","Accept":"application/csv"});raw=urlopen(req,timeout=20).read().decode()
                for r in csv.DictReader(io.StringIO(raw)):
                    try:v=float(r.get("_value",""))
                    except ValueError:continue
                    history.setdefault(f"{si}:{r.get('_field','')}",[]).append({"time":r.get("_time"),"value":v})
            except Exception:pass
    now_iso=datetime.now(timezone.utc).isoformat()
    # Extend history to "now" with matching live signals when the same technical key exists.
    # This avoids a stale-looking graph between CSV/history imports.
    for b in cfg.get("blocks",[]):
        for it in b.get("items",[]):
            if it.get("scope")!="history" or not it.get("key"):continue
            si=int(it.get("source_index",0));hist_key=f"{si}:{it['key']}"
            live_key=hist_key
            if live_key in live:
                raw=live[live_key]
                try:
                    num=float(str(raw).replace(" °C","").replace(" %","").replace(",","."))
                    history.setdefault(hist_key,[]).append({"time":now_iso,"value":num})
                except Exception:pass
    last_history=max((p.get("time","") for pts in history.values() for p in pts),default=None)
    return {"id":row["id"],"name":row["name"],"description":cfg.get("description",""),"blocks":cfg.get("blocks",[]),"sources":sources,"live":live,"history":history,"period":period,"last_history":last_history}

@app.get("/dev-portal/preview/{config_id}")
def customer_portal_dev_preview(config_id:int,ins_service_session: str | None = Cookie(default=None)):
    if not _service_session_valid(ins_service_session):raise HTTPException(401,"SERVICE_LOGIN_REQUIRED")
    path=CUSTOMER_PORTAL_DEV_DIR/"preview.html"
    if not path.exists():raise HTTPException(404,"PREVIEW_NOT_DEPLOYED")
    return FileResponse(path)

@app.get("/dev-portal")
@app.get("/dev-portal/")
def customer_portal_dev_index(ins_service_session: str | None = Cookie(default=None)):
    if not _service_session_valid(ins_service_session):
        raise HTTPException(401,"SERVICE_LOGIN_REQUIRED")
    path = CUSTOMER_PORTAL_DEV_DIR / "index.html"
    if not path.exists(): raise HTTPException(404,"CUSTOMER_PORTAL_DEV_NOT_DEPLOYED")
    return FileResponse(path)

@app.get("/dev-portal/{asset_name}")
def customer_portal_dev_asset(asset_name: str, ins_service_session: str | None = Cookie(default=None)):
    if not _service_session_valid(ins_service_session): raise HTTPException(401,"SERVICE_LOGIN_REQUIRED")
    if asset_name not in {"portal.css","portal.js","preview.js"}: raise HTTPException(404,"PORTAL_ASSET_NOT_FOUND")
    path=CUSTOMER_PORTAL_DEV_DIR/asset_name
    if not path.exists(): raise HTTPException(404,"CUSTOMER_PORTAL_DEV_NOT_DEPLOYED")
    return FileResponse(path)
