import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import FastAPI, Request, Form
from fastapi.responses import PlainTextResponse, JSONResponse, HTMLResponse, RedirectResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, func
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from passlib.hash import pbkdf2_sha256
from itsdangerous import URLSafeSerializer, BadSignature

# ========== ENV ==========
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "gourav_wa_verify_2025")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
GRAPH_VERSION = "v19.0"

DB_URL = os.getenv("DB_URL", "sqlite:///./inbox.db")
SESSION_SECRET = os.getenv("SESSION_SECRET", "bar291yet071")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@beyond.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
AGENT1_EMAIL = os.getenv("AGENT1_EMAIL", "agent1@beyond.com")
AGENT1_PASSWORD = os.getenv("AGENT1_PASSWORD", "agent123")
AGENT2_EMAIL = os.getenv("AGENT2_EMAIL", "agent2@beyond.com")
AGENT2_PASSWORD = os.getenv("AGENT2_PASSWORD", "agent123")

# ========== DB ==========
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class Agent(Base):
    __tablename__ = "agents"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)  # admin/agent
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True)
    wa_id = Column(String(32), unique=True, index=True, nullable=False)  # customer number
    customer_name = Column(String(255), nullable=True)
    status = Column(String(20), default="open")  # open/resolved
    mode = Column(String(10), default="ai")      # ai/human  <-- handoff switch
    assigned_agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    assigned_agent = relationship("Agent")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), index=True, nullable=False)
    direction = Column(String(10), nullable=False)  # inbound/outbound
    message_id = Column(String(128), unique=True, index=True, nullable=True)
    text = Column(Text, nullable=True)
    ts_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    sent_by_agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)
    sent_by_ai = Column(Integer, default=0)  # 1/0

    conversation = relationship("Conversation", back_populates="messages")
    sent_by_agent = relationship("Agent")

Base.metadata.create_all(bind=engine)

# ========== AUTH ==========
serializer = URLSafeSerializer(SESSION_SECRET, salt="team-inbox")

def set_session(resp: HTMLResponse, agent_id: int):
    token = serializer.dumps({"agent_id": agent_id})
    resp.set_cookie("session", token, httponly=True, samesite="lax")

def clear_session(resp: HTMLResponse):
    resp.delete_cookie("session")

def get_current_agent(request: Request) -> Optional[Agent]:
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        data = serializer.loads(token)
        agent_id = data.get("agent_id")
        if not agent_id:
            return None
        db = SessionLocal()
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        db.close()
        return agent
    except BadSignature:
        return None

def require_login(request: Request) -> Optional[Agent]:
    agent = get_current_agent(request)
    return agent

def seed_agents():
    db = SessionLocal()

    def upsert(email, name, role, password):
        # If already exists, don't insert again
        existing = db.query(Agent).filter(Agent.email == email).first()
        if existing:
            return

        db.add(Agent(
            email=email,
            name=name,
            role=role,
            password_hash=pbkdf2_sha256.hash(password),
        ))

    upsert(ADMIN_EMAIL, "Admin", "admin", ADMIN_PASSWORD)
    upsert(AGENT1_EMAIL, "Agent 1", "agent", AGENT1_PASSWORD)
    upsert(AGENT2_EMAIL, "Agent 2", "agent", AGENT2_PASSWORD)

    try:
        db.commit()
    except Exception:
        # In case of a rare race condition on deploy/restart
        db.rollback()
    finally:
        db.close()


seed_agents()

# ========== APP ==========
app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}

# --- WhatsApp webhook verification ---
@app.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    qp = request.query_params
    mode = qp.get("hub.mode")
    token = qp.get("hub.verify_token")
    challenge = qp.get("hub.challenge")

    if not mode and not token and not challenge:
        return PlainTextResponse("OK")

    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return PlainTextResponse(challenge)

    return PlainTextResponse("Verification failed", status_code=403)

def wa_send_text(to: str, text: str):
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text[:4096]}}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    return r.status_code, r.text

# --- Plug your AI here (keep your existing OpenAI function if you already have it) ---
def get_ai_reply(conversation_id: int, user_text: str) -> str:
    # Replace with your existing AI call:
    # return your_openai_reply(...)
    return f"AI reply placeholder: {user_text}"

def upsert_conversation(db, wa_id: str, customer_name: Optional[str]) -> Conversation:
    conv = db.query(Conversation).filter(Conversation.wa_id == wa_id).first()
    now = datetime.now(timezone.utc)
    if not conv:
        conv = Conversation(wa_id=wa_id, customer_name=customer_name, last_message_at=now)
        db.add(conv)
        db.flush()
    else:
        if customer_name and not conv.customer_name:
            conv.customer_name = customer_name
        conv.last_message_at = now
    return conv

def save_message(db, conv_id: int, direction: str, text: str, message_id: Optional[str], sent_by_agent_id=None, sent_by_ai=0, ts=None):
    if ts is None:
        ts = datetime.now(timezone.utc)
    # avoid duplicates for inbound message_id
    if message_id:
        exists = db.query(Message).filter(Message.message_id == message_id).first()
        if exists:
            return exists
    m = Message(
        conversation_id=conv_id,
        direction=direction,
        text=text,
        message_id=message_id,
        ts_utc=ts,
        sent_by_agent_id=sent_by_agent_id,
        sent_by_ai=sent_by_ai,
    )
    db.add(m)
    return m

# --- Incoming WhatsApp events ---
@app.post("/webhook/whatsapp")
async def receive_webhook(request: Request):
    payload = await request.json()

    try:
        changes = payload["entry"][0]["changes"][0]["value"]
        messages = changes.get("messages", [])
        contacts = changes.get("contacts", [])

        if not messages:
            return JSONResponse({"status": "ok"})  # statuses etc.

        msg = messages[0]
        from_number = msg.get("from")
        text_body = (msg.get("text") or {}).get("body", "").strip()
        msg_id = msg.get("id")
        ts = datetime.fromtimestamp(int(msg.get("timestamp")), tz=timezone.utc)

        customer_name = None
        if contacts:
            customer_name = (contacts[0].get("profile", {}) or {}).get("name")

        if not from_number or not text_body:
            return JSONResponse({"status": "ok"})

        db = SessionLocal()
        conv = upsert_conversation(db, from_number, customer_name)
        save_message(db, conv.id, "inbound", text_body, msg_id, ts=ts)
        db.commit()

        # HUMAN HANDOFF: if conversation in human mode, do NOT auto-reply
        conv = db.query(Conversation).filter(Conversation.id == conv.id).first()
        if conv.mode == "human":
            db.close()
            return JSONResponse({"status": "ok"})

        # AI mode -> reply
        ai_text = get_ai_reply(conv.id, text_body)
        status_code, resp_text = wa_send_text(from_number, ai_text)

        # store outbound
        save_message(db, conv.id, "outbound", ai_text, None, sent_by_ai=1, ts=datetime.now(timezone.utc))
        db.commit()
        db.close()

        return JSONResponse({"status": "ok", "wa_send_status": status_code})
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)})

# ========== TEAM INBOX UI ==========
LOGIN_HTML = """
<html><body style="font-family:Arial;padding:20px;max-width:420px">
<h2>Team Inbox Login</h2>
<form method="post" action="/login">
<label>Email</label><br/>
<input name="email" style="width:100%;padding:8px" /><br/><br/>
<label>Password</label><br/>
<input name="password" type="password" style="width:100%;padding:8px" /><br/><br/>
<button style="padding:10px 14px">Login</button>
</form>
</body></html>
"""

@app.get("/login", response_class=HTMLResponse)
def login_page():
    return HTMLResponse(LOGIN_HTML)

@app.post("/login")
def login(email: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    agent = db.query(Agent).filter(Agent.email == email).first()
    db.close()
    if not agent or not pbkdf2_sha256.verify(password, agent.password_hash):
        return HTMLResponse("<h3>Invalid credentials</h3><a href='/login'>Try again</a>", status_code=401)
    resp = RedirectResponse("/inbox", status_code=302)
    set_session(resp, agent.id)
    return resp

@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    clear_session(resp)
    return resp

@app.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request):
    agent = require_login(request)
    if not agent:
        return RedirectResponse("/login", status_code=302)

    db = SessionLocal()
    convs = (
        db.query(Conversation)
        .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
        .limit(50)
        .all()
    )
    db.close()

    rows = ""
    for c in convs:
        assignee = f"{c.assigned_agent.name}" if c.assigned_agent else ""
        rows += f"""
        <tr>
          <td><a href="/inbox/chat/{c.id}">{c.wa_id}</a></td>
          <td>{(c.customer_name or "")}</td>
          <td>{c.status}</td>
          <td>{c.mode}</td>
          <td>{assignee}</td>
          <td>{c.last_message_at}</td>
        </tr>
        """

    return HTMLResponse(f"""
    <html><body style="font-family:Arial;padding:16px">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <h2>Team Inbox</h2>
      <div>Logged in: <b>{agent.email}</b> ({agent.role}) | <a href="/logout">Logout</a></div>
    </div>
    <table border="1" cellpadding="8" cellspacing="0">
      <tr><th>WA ID</th><th>Name</th><th>Status</th><th>Mode</th><th>Assigned</th><th>Last Msg</th></tr>
      {rows}
    </table>
    </body></html>
    """)

@app.get("/inbox/chat/{conv_id}", response_class=HTMLResponse)
def chat_view(request: Request, conv_id: int):
    agent = require_login(request)
    if not agent:
        return RedirectResponse("/login", status_code=302)

    db = SessionLocal()
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    msgs = db.query(Message).filter(Message.conversation_id == conv_id).order_by(Message.ts_utc.asc()).all()
    db.close()
    if not conv:
        return HTMLResponse("Conversation not found", status_code=404)

    msg_html = ""
    for m in msgs:
        who = "Customer" if m.direction == "inbound" else ("AI" if m.sent_by_ai else "Agent")
        msg_html += f"<div style='margin:6px 0'><b>{who}:</b> {m.text}</div>"

    assigned = conv.assigned_agent.name if conv.assigned_agent else "Unassigned"

    return HTMLResponse(f"""
    <html><body style="font-family:Arial;padding:16px;max-width:900px">
    <a href="/inbox">← Back</a>
    <h2>Chat: {conv.wa_id} {(conv.customer_name or "")}</h2>
    <p>Status: <b>{conv.status}</b> | Mode: <b>{conv.mode}</b> | Assigned: <b>{assigned}</b></p>

    <form method="post" action="/api/conversations/{conv.id}/assign">
      <button>Assign to me</button>
    </form>

    <form method="post" action="/api/conversations/{conv.id}/mode" style="margin-top:8px">
      <input type="hidden" name="mode" value="human"/>
      <button>Handoff to Human</button>
    </form>

    <form method="post" action="/api/conversations/{conv.id}/mode" style="margin-top:8px">
      <input type="hidden" name="mode" value="ai"/>
      <button>Return to AI</button>
    </form>

    <hr/>
    <div style="background:#f6f6f6;padding:12px;border-radius:8px">{msg_html}</div>

    <hr/>
    <h3>Reply (Agent)</h3>
    <form method="post" action="/api/conversations/{conv.id}/reply">
      <textarea name="text" style="width:100%;height:90px;padding:8px"></textarea><br/>
      <button style="margin-top:8px;padding:10px 14px">Send</button>
    </form>

    </body></html>
    """)

# ========== INBOX ACTIONS ==========
@app.post("/api/conversations/{conv_id}/assign")
def assign_to_me(request: Request, conv_id: int):
    agent = require_login(request)
    if not agent:
        return RedirectResponse("/login", status_code=302)

    db = SessionLocal()
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if conv:
        conv.assigned_agent_id = agent.id
        conv.mode = "human"  # assignment implies human takeover
        db.commit()
    db.close()
    return RedirectResponse(f"/inbox/chat/{conv_id}", status_code=302)

@app.post("/api/conversations/{conv_id}/mode")
def set_mode(request: Request, conv_id: int, mode: str = Form(...)):
    agent = require_login(request)
    if not agent:
        return RedirectResponse("/login", status_code=302)

    if mode not in ("ai", "human"):
        return HTMLResponse("Invalid mode", status_code=400)

    db = SessionLocal()
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if conv:
        conv.mode = mode
        db.commit()
    db.close()
    return RedirectResponse(f"/inbox/chat/{conv_id}", status_code=302)

@app.post("/api/conversations/{conv_id}/reply")
def agent_reply(request: Request, conv_id: int, text: str = Form(...)):
    agent = require_login(request)
    if not agent:
        return RedirectResponse("/login", status_code=302)

    db = SessionLocal()
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        db.close()
        return HTMLResponse("Conversation not found", status_code=404)

    # Basic concurrency rule: if assigned and not you, block
    if conv.assigned_agent_id and conv.assigned_agent_id != agent.id and agent.role != "admin":
        db.close()
        return HTMLResponse("This chat is assigned to another agent.", status_code=403)

    conv.mode = "human"  # sending manual reply implies human mode
    conv.assigned_agent_id = conv.assigned_agent_id or agent.id

    status_code, resp_text = wa_send_text(conv.wa_id, text)

    save_message(
        db,
        conv.id,
        "outbound",
        text,
        message_id=None,
        sent_by_agent_id=agent.id,
        sent_by_ai=0,
        ts=datetime.now(timezone.utc),
    )
    conv.last_message_at = datetime.now(timezone.utc)
    db.commit()
    db.close()

    return RedirectResponse(f"/inbox/chat/{conv_id}", status_code=302)
