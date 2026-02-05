"""
NxMx StockExec AI — Single-file FastAPI app (Render-ready)
---------------------------------------------------------
✅ WhatsApp Cloud API webhook (verify + inbound)
✅ Menu-driven bot (Meta interactive list)
✅ Multi-tenant (basic) + Feature Flags + Pricing enforcement (auto-disable)
✅ SQLite by default (local.db) OR Postgres via DATABASE_URL
✅ Lightweight Admin Dashboard (HTML) + Admin APIs

Render Start Command:
  uvicorn app:app --host 0.0.0.0 --port $PORT

Local Run:
  pip install fastapi uvicorn[standard] sqlalchemy psycopg[binary] python-dotenv requests
  uvicorn app:app --reload --port 8000

ENV (.env):
  DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db   (optional)
  WHATSAPP_TOKEN=EAA...
  WHATSAPP_PHONE_NUMBER_ID=123456789012345
  WHATSAPP_VERIFY_TOKEN=nxmx_verify_token
  ADMIN_TOKEN=change_me_strong_token
  DEFAULT_TENANT_ID=1
"""

import os
import json
import datetime as dt
from typing import Dict, Set, Optional, Tuple

import requests
from fastapi import FastAPI, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from dotenv import load_dotenv

from sqlalchemy import (
    create_engine, String, Integer, Boolean, DateTime, ForeignKey, Text, func, select
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, Session
)

# --------------------------
# Config
# --------------------------
load_dotenv()

DATABASE_URL = os.getenv("DB_URL") or "sqlite:///./local.db"
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "change_me")
DEFAULT_TENANT_ID = int(os.getenv("DEFAULT_TENANT_ID", "1"))

GRAPH_URL = "https://graph.facebook.com/v20.0"

# --------------------------
# Pricing + Feature Flags
# --------------------------
F_MARKET_BRIEF = "F_MARKET_BRIEF"
F_WHY_MARKET_MOVED = "F_WHY_MARKET_MOVED"
F_RISK_RADAR = "F_RISK_RADAR"
F_CALL_PRIORITY = "F_CALL_PRIORITY"
F_SEBI_ADVISORY = "F_SEBI_ADVISORY"
F_CLIENT_AI = "F_CLIENT_AI"
F_CALL_AI = "F_CALL_AI"
F_VOICE_REPLY = "F_VOICE_REPLY"
F_COMPLIANCE_LOG = "F_COMPLIANCE_LOG"

PLAN_FEATURES: Dict[str, Set[str]] = {
    "starter": {F_MARKET_BRIEF, F_WHY_MARKET_MOVED, F_SEBI_ADVISORY, F_CLIENT_AI, F_COMPLIANCE_LOG},
    "pro": {F_MARKET_BRIEF, F_WHY_MARKET_MOVED, F_RISK_RADAR, F_CALL_PRIORITY, F_SEBI_ADVISORY, F_CLIENT_AI, F_COMPLIANCE_LOG},
    "elite": {F_MARKET_BRIEF, F_WHY_MARKET_MOVED, F_RISK_RADAR, F_CALL_PRIORITY, F_SEBI_ADVISORY, F_CLIENT_AI, F_CALL_AI, F_VOICE_REPLY, F_COMPLIANCE_LOG},
    "enterprise": {F_MARKET_BRIEF, F_WHY_MARKET_MOVED, F_RISK_RADAR, F_CALL_PRIORITY, F_SEBI_ADVISORY, F_CLIENT_AI, F_CALL_AI, F_VOICE_REPLY, F_COMPLIANCE_LOG},
}

DEFAULT_FLAGS_ON: Dict[str, bool] = {
    F_MARKET_BRIEF: True,
    F_WHY_MARKET_MOVED: True,
    F_SEBI_ADVISORY: True,
    F_CLIENT_AI: True,
    F_COMPLIANCE_LOG: True,
    F_RISK_RADAR: False,
    F_CALL_PRIORITY: False,
    F_CALL_AI: False,
    F_VOICE_REPLY: False,
}


def allowed_features(plan: str) -> Set[str]:
    return PLAN_FEATURES.get((plan or "").lower(), PLAN_FEATURES["starter"])


# --------------------------
# DB setup
# --------------------------
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="Unnamed Tenant")
    whatsapp_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    plan: Mapped[str] = mapped_column(String(30), nullable=False, default="starter")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    flags: Mapped[list["FeatureFlag"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class FeatureFlag(Base):
    __tablename__ = "feature_flags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    flag_key: Mapped[str] = mapped_column(String(80), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="flags")


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="Client")
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    panic_score: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class MessageLog(Base):
    __tablename__ = "message_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    wa_from: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    wa_to: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    direction: Mapped[str] = mapped_column(String(10))  # inbound/outbound
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)


def ensure_default_tenant(db: Session, tenant_id: int = 1) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        tenant = Tenant(id=tenant_id, name="Default Tenant", plan="starter")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

    existing = set(db.scalars(select(FeatureFlag.flag_key).where(FeatureFlag.tenant_id == tenant.id)).all())
    for k, v in DEFAULT_FLAGS_ON.items():
        if k not in existing:
            db.add(FeatureFlag(tenant_id=tenant.id, flag_key=k, enabled=bool(v)))
    db.commit()
    return tenant


def get_flags(db: Session, tenant_id: int) -> Dict[str, bool]:
    rows = db.scalars(select(FeatureFlag).where(FeatureFlag.tenant_id == tenant_id)).all()
    return {r.flag_key: bool(r.enabled) for r in rows}


def set_flag(db: Session, tenant_id: int, flag_key: str, enabled: bool) -> FeatureFlag:
    row = db.scalar(select(FeatureFlag).where(FeatureFlag.tenant_id == tenant_id, FeatureFlag.flag_key == flag_key))
    if not row:
        row = FeatureFlag(tenant_id=tenant_id, flag_key=flag_key, enabled=enabled)
        db.add(row)
    else:
        row.enabled = enabled
    db.commit()
    db.refresh(row)
    return row


def enforce_plan(db: Session, tenant_id: int) -> Dict[str, bool]:
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        return {}
    allow = allowed_features(tenant.plan)
    rows = db.scalars(select(FeatureFlag).where(FeatureFlag.tenant_id == tenant_id)).all()
    changed = False
    for r in rows:
        if r.enabled and r.flag_key not in allow:
            r.enabled = False
            changed = True
    if changed:
        db.commit()
    return {r.flag_key: bool(r.enabled) for r in rows}


def is_enabled(db: Session, tenant_id: int, flag_key: str) -> bool:
    row = db.scalar(select(FeatureFlag).where(FeatureFlag.tenant_id == tenant_id, FeatureFlag.flag_key == flag_key))
    return bool(row.enabled) if row else False


def log_message(db: Session, tenant_id: int, wa_from: str, wa_to: str, direction: str, message: str):
    db.add(MessageLog(
        tenant_id=tenant_id,
        wa_from=wa_from,
        wa_to=wa_to,
        direction=direction,
        message=message,
    ))
    db.commit()


# --------------------------
# WhatsApp helpers
# --------------------------
def wa_headers():
    return {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}


def send_text(to: str, text: str):
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {"skipped": True, "reason": "Missing WHATSAPP_TOKEN/WHATSAPP_PHONE_NUMBER_ID", "to": to, "text": text}

    url = f"{GRAPH_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    r = requests.post(url, headers=wa_headers(), json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def send_menu(to: str, menu_payload: dict):
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {"skipped": True, "reason": "Missing WHATSAPP_TOKEN/WHATSAPP_PHONE_NUMBER_ID", "to": to, "menu": menu_payload}

    url = f"{GRAPH_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {"messaging_product": "whatsapp", "to": to, **menu_payload}
    r = requests.post(url, headers=wa_headers(), json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


MENU_PAYLOAD = {
    "type": "interactive",
    "interactive": {
        "type": "list",
        "header": {"type": "text", "text": "NxMx StockExec AI"},
        "body": {"text": "Please choose an option 👇"},
        "footer": {"text": "WhatsApp Control Room"},
        "action": {
            "button": "Menu",
            "sections": [{
                "title": "Main Menu",
                "rows": [
                    {"id": "MARKET_BRIEF", "title": "Today's Market Brief"},
                    {"id": "WHY_MARKET_MOVED", "title": "Why Market Moved"},
                    {"id": "RISK_ALERTS", "title": "Client Risk Alerts"},
                    {"id": "CALL_PRIORITY", "title": "Who Should I Call Now"},
                    {"id": "SEBI_ADVISORY", "title": "SEBI-safe Advisory"},
                    {"id": "CLIENT_AI", "title": "Client Query Assistant"},
                    {"id": "CALL_SUMMARY", "title": "Call & Activity Summaries"},
                    {"id": "SETTINGS", "title": "Settings / Features"},
                ]
            }]
        }
    }
}


def parse_inbound(payload: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    entry = payload["entry"][0]["changes"][0]["value"]
    messages = entry.get("messages", [])
    if not messages:
        return None, None, None

    msg = messages[0]
    wa_from = msg.get("from", "")
    wa_to = entry.get("metadata", {}).get("display_phone_number", "")

    text_body = ""
    mtype = msg.get("type")
    if mtype == "text":
        text_body = msg["text"]["body"].strip()
    elif mtype == "interactive":
        inter = msg.get("interactive", {})
        itype = inter.get("type")
        if itype == "list_reply":
            text_body = inter["list_reply"]["id"]
        elif itype == "button_reply":
            text_body = inter["button_reply"]["id"]
    return wa_from, wa_to, text_body


# --------------------------
# App
# --------------------------
app = FastAPI(title="NxMx StockExec AI (single-file)", version="1.0.0")


@app.on_event("startup")
def _startup():
    init_db()
    db = SessionLocal()
    try:
        ensure_default_tenant(db, DEFAULT_TENANT_ID)
    finally:
        db.close()


@app.get("/")
def root():
    return {"ok": True, "service": "NxMx StockExec AI", "db": "ok"}


# --------------------------
# WhatsApp Webhook
# --------------------------
@app.get("/webhook/whatsapp")
def wa_verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(content=str(challenge))
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook/whatsapp")
async def wa_inbound(payload: dict, db: Session = Depends(get_db)):
    tenant_id = DEFAULT_TENANT_ID
    enforce_plan(db, tenant_id)  # pricing enforcement on every message

    try:
        wa_from, wa_to, body = parse_inbound(payload)
        if not wa_from or not body:
            return {"ok": True}

        if is_enabled(db, tenant_id, F_COMPLIANCE_LOG):
            log_message(db, tenant_id, wa_from, wa_to or "", "inbound", body)

        if body.lower() in {"hi", "hello", "menu", "start"}:
            send_menu(wa_from, MENU_PAYLOAD)
            if is_enabled(db, tenant_id, F_COMPLIANCE_LOG):
                log_message(db, tenant_id, wa_from, wa_to or "", "outbound", "MENU_SENT")
            return {"ok": True}

        if body == "MARKET_BRIEF":
            if not is_enabled(db, tenant_id, F_MARKET_BRIEF):
                send_text(wa_from, "🔒 Market Brief is not enabled on your plan.")
                return {"ok": True}
            send_text(wa_from, "📌 Market Brief (demo)\n• NIFTY: -0.42%\n• BANKNIFTY: Weak\n• FII: Net sellers\n\n(Connect live data feed next)")
            return {"ok": True}

        if body == "WHY_MARKET_MOVED":
            if not is_enabled(db, tenant_id, F_WHY_MARKET_MOVED):
                send_text(wa_from, "🔒 Why Market Moved is not enabled on your plan.")
                return {"ok": True}
            send_text(wa_from, "🧠 Why Market Moved (demo)\nOI unwinding + global yield move.\n(Connect news + derivatives feed next)")
            return {"ok": True}

        if body == "RISK_ALERTS":
            if not is_enabled(db, tenant_id, F_RISK_RADAR):
                send_text(wa_from, "🔒 Risk Radar is a Pro feature. Reply 'Upgrade' to enable.")
                return {"ok": True}
            send_text(wa_from, "🔴 Risk Alerts (demo)\n• Client A: high margin usage\n• Client B: panic pattern\n(Connect client trades next)")
            return {"ok": True}

        if body == "CALL_PRIORITY":
            if not is_enabled(db, tenant_id, F_CALL_PRIORITY):
                send_text(wa_from, "🔒 Call Priority is a Pro feature. Reply 'Upgrade' to enable.")
                return {"ok": True}
            send_text(wa_from, "📞 Priority Calls (demo)\n1) Client X — drawdown\n2) Client Y — expiry risk\n3) Client Z — panic history")
            return {"ok": True}

        if body == "SEBI_ADVISORY":
            if not is_enabled(db, tenant_id, F_SEBI_ADVISORY):
                send_text(wa_from, "🔒 SEBI Advisory Generator is not enabled on your plan.")
                return {"ok": True}
            send_text(wa_from, "✅ Paste the message you want to rewrite in SEBI-safe language (demo).")
            return {"ok": True}

        if body == "CLIENT_AI":
            if not is_enabled(db, tenant_id, F_CLIENT_AI):
                send_text(wa_from, "🔒 Client Query Assistant is not enabled on your plan.")
                return {"ok": True}
            send_text(wa_from, "🤖 Client Query Assistant (demo)\nAsk like: 'Reliance ka kya karu?'\n(Connect portfolio + risk profile next)")
            return {"ok": True}

        if body == "CALL_SUMMARY":
            if not is_enabled(db, tenant_id, F_CALL_AI):
                send_text(wa_from, "🔒 Call AI summaries are an Elite feature. Reply 'Upgrade' to enable.")
                return {"ok": True}
            send_text(wa_from, "📞 Call Summary (demo)\nEmotion: anxious\nRisky promises: none\nFollow-up: suggested")
            return {"ok": True}

        if body == "SETTINGS" or body.lower() in {"settings", "upgrade"}:
            t = db.get(Tenant, tenant_id)
            flags = get_flags(db, tenant_id)
            enabled = [k.replace("F_", "") for k, v in flags.items() if v]
            send_text(wa_from, f"⚙️ Current Plan: {t.plan if t else 'starter'}\nEnabled: {', '.join(enabled) if enabled else '(none)'}\n\nAdmin can upgrade from dashboard.")
            return {"ok": True}

        # Basic SEBI-safe rewrite demo if user pastes risky wording
        if is_enabled(db, tenant_id, F_SEBI_ADVISORY) and len(body) > 15 and any(x in body.lower() for x in ["guarantee", "sure", "100%", "fixed return"]):
            safe = "✅ SEBI-safe version:\n“This is market-linked and subject to risk. Please consider your risk profile before investing.”\n\n(Connect your exact templates next)"
            send_text(wa_from, safe)
            return {"ok": True}

        send_text(wa_from, "Reply 'Menu' to see options.")
        return {"ok": True}

    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --------------------------
# Minimal Admin Auth helper
# --------------------------
def require_admin(request: Request):
    token = request.query_params.get("token") or request.headers.get("x-admin-token")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


# --------------------------
# Admin endpoints (used by dashboard forms)
# --------------------------
@app.post("/admin/tenants")
def admin_create_tenant(
    request: Request,
    name: str = Form(...),
    plan: str = Form("starter"),
    whatsapp_number: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin(request)
    t = Tenant(name=name, plan=plan, whatsapp_number=whatsapp_number or None)
    db.add(t)
    db.commit()
    db.refresh(t)
    for k, v in DEFAULT_FLAGS_ON.items():
        set_flag(db, t.id, k, bool(v))
    enforce_plan(db, t.id)
    return RedirectResponse(url=f"/dashboard?token={ADMIN_TOKEN}", status_code=303)


@app.post("/admin/tenants/{tenant_id}/plan")
def admin_set_plan(request: Request, tenant_id: int, plan: str = Form(...), db: Session = Depends(get_db)):
    require_admin(request)
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "tenant not found")
    t.plan = plan
    db.commit()
    enforce_plan(db, tenant_id)
    return RedirectResponse(url=f"/dashboard?token={ADMIN_TOKEN}&tenant_id={tenant_id}", status_code=303)


@app.post("/admin/tenants/{tenant_id}/flags/{flag_key}")
def admin_set_flag(request: Request, tenant_id: int, flag_key: str, enabled: str = Form("false"), db: Session = Depends(get_db)):
    require_admin(request)
    val = enabled.lower() in {"1", "true", "yes", "on"}
    set_flag(db, tenant_id, flag_key, val)
    enforce_plan(db, tenant_id)
    return RedirectResponse(url=f"/dashboard?token={ADMIN_TOKEN}&tenant_id={tenant_id}", status_code=303)


@app.get("/admin/tenants/{tenant_id}/flags")
def admin_list_flags(request: Request, tenant_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    return {"tenant_id": tenant_id, "flags": get_flags(db, tenant_id)}


# --------------------------
# Admin Dashboard (HTML)
# --------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, tenant_id: int = 0, db: Session = Depends(get_db)):
    require_admin(request)

    tenants = db.scalars(select(Tenant).order_by(Tenant.id.asc())).all()
    if not tenants:
        ensure_default_tenant(db, DEFAULT_TENANT_ID)
        tenants = db.scalars(select(Tenant).order_by(Tenant.id.asc())).all()

    selected = db.get(Tenant, tenant_id) if tenant_id else tenants[0]
    ensure_default_tenant(db, selected.id)
    enforce_plan(db, selected.id)
    flags = get_flags(db, selected.id)

    msg_count = db.scalar(select(func.count(MessageLog.id)).where(MessageLog.tenant_id == selected.id)) or 0
    last_msg = db.scalar(select(func.max(MessageLog.created_at)).where(MessageLog.tenant_id == selected.id))
    last_msg_str = last_msg.isoformat() if last_msg else "—"

    tenant_options = "".join(
        f'<option value="{t.id}" {"selected" if t.id == selected.id else ""}>#{t.id} — {t.name} ({t.plan})</option>'
        for t in tenants
    )

    feature_rows = ""
    for k in sorted(flags.keys()):
        next_val = "false" if flags[k] else "true"
        feature_rows += f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #2b3a63;">{k}</td>
          <td style="padding:8px;border-bottom:1px solid #2b3a63;text-align:center;">
            <form method="post" action="/admin/tenants/{selected.id}/flags/{k}?token={ADMIN_TOKEN}">
              <input type="hidden" name="enabled" value="{next_val}" />
              <button class="btn {'btn-on' if flags[k] else 'btn-off'}" type="submit">{'ON' if flags[k] else 'OFF'}</button>
            </form>
          </td>
        </tr>
        """

    html = f"""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <title>NxMx StockExec AI — Dashboard</title>
      <style>
        body {{ background:#0b1220; color:#e5e7eb; font-family: Inter, Arial, sans-serif; margin:0; }}
        .wrap {{ max-width:1100px; margin:0 auto; padding:24px; }}
        .card {{ background:#111a2e; border:1px solid #243152; border-radius:16px; padding:18px; box-shadow:0 10px 24px rgba(0,0,0,.25);}}
        .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
        h1 {{ margin:0 0 10px; font-size:26px; }}
        h2 {{ margin:0 0 10px; font-size:18px; color:#c7d2fe; }}
        .muted {{ color:#9ca3af; }}
        .row {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
        select, input, button {{ border-radius:12px; border:1px solid #2b3a63; padding:10px 12px; background:#0b1220; color:#e5e7eb; }}
        .btn {{ cursor:pointer; font-weight:800; }}
        .btn-on {{ background:#0a7a54; border-color:#0a7a54; }}
        .btn-off {{ background:#8a2b2b; border-color:#8a2b2b; }}
        table {{ width:100%; border-collapse:collapse; }}
        .pill {{ display:inline-block; padding:6px 10px; border-radius:999px; background:#0b1220; border:1px solid #243152; }}
        a {{ color:#93c5fd; text-decoration:none; }}
      </style>
    </head>
    <body>
      <div class="wrap">
        <div class="row" style="justify-content:space-between;">
          <div>
            <div style="font-size:22px;font-weight:900;">NxMx StockExec AI — Admin Dashboard</div>
            <div class="muted" style="font-size:13px;">Single-file • Plan enforcement • Feature flags</div>
          </div>
          <div class="row">
            <span class="pill">Tenant #{selected.id}: <b>{selected.name}</b></span>
            <span class="pill">Plan: <b>{selected.plan}</b></span>
            <span class="pill">Messages: <b>{msg_count}</b></span>
            <span class="pill">Last Msg: <b>{last_msg_str}</b></span>
          </div>
        </div>

        <div style="height:16px"></div>

        <div class="grid">
          <div class="card">
            <h2>Select Tenant</h2>
            <form method="get" action="/dashboard">
              <input type="hidden" name="token" value="{ADMIN_TOKEN}"/>
              <div class="row">
                <select name="tenant_id" onchange="this.form.submit()">{tenant_options}</select>
                <noscript><button class="btn" type="submit">Open</button></noscript>
              </div>
            </form>

            <div style="height:14px"></div>

            <h2>Change Plan (auto-disables disallowed features)</h2>
            <form method="post" action="/admin/tenants/{selected.id}/plan?token={ADMIN_TOKEN}">
              <div class="row">
                <select name="plan">
                  <option value="starter" {"selected" if selected.plan=="starter" else ""}>starter</option>
                  <option value="pro" {"selected" if selected.plan=="pro" else ""}>pro</option>
                  <option value="elite" {"selected" if selected.plan=="elite" else ""}>elite</option>
                  <option value="enterprise" {"selected" if selected.plan=="enterprise" else ""}>enterprise</option>
                </select>
                <button class="btn" type="submit">Update Plan</button>
              </div>
            </form>

            <div style="height:14px"></div>

            <h2>Create New Tenant</h2>
            <form method="post" action="/admin/tenants?token={ADMIN_TOKEN}">
              <div class="row">
                <input name="name" placeholder="Tenant name" required />
                <input name="whatsapp_number" placeholder="WhatsApp number (optional)" />
                <select name="plan">
                  <option value="starter">starter</option>
                  <option value="pro">pro</option>
                  <option value="elite">elite</option>
                  <option value="enterprise">enterprise</option>
                </select>
                <button class="btn" type="submit">Create</button>
              </div>
            </form>

            <div style="height:14px"></div>
            <div class="muted" style="font-size:13px;">
              Webhook: <code>/webhook/whatsapp</code><br/>
              Tip: Use <code>?token=YOUR_ADMIN_TOKEN</code> to open dashboard.
            </div>
          </div>

          <div class="card">
            <h2>Feature Flags (effective after enforcement)</h2>
            <div class="muted" style="font-size:13px;">If plan doesn't allow a feature, it will turn OFF automatically.</div>
            <div style="height:10px"></div>
            <table>
              <thead>
                <tr>
                  <th style="text-align:left;padding:8px;border-bottom:1px solid #2b3a63;">Feature</th>
                  <th style="text-align:center;padding:8px;border-bottom:1px solid #2b3a63;">Toggle</th>
                </tr>
              </thead>
              <tbody>{feature_rows}</tbody>
            </table>

            <div style="height:12px"></div>
            <div class="row">
              <a href="/admin/tenants/{selected.id}/flags?token={ADMIN_TOKEN}" target="_blank">View Flags JSON</a>
              <span class="muted">|</span>
              <a href="/" target="_blank">Health</a>
              <span class="muted">|</span>
              <a href="/debug/menu" target="_blank">Menu JSON</a>
            </div>
          </div>
        </div>

      </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/debug/menu")
def debug_menu():
    return MENU_PAYLOAD
