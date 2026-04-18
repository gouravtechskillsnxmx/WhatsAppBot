import os
import json
import sqlite3
from datetime import datetime
from html import escape
from typing import Optional, Tuple
from urllib.parse import quote

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, HTMLResponse

app = FastAPI()

# =========================================================
# ENV
# =========================================================
EXO_API_KEY = os.getenv("EXO_API_KEY", "").strip()
EXO_SID = os.getenv("EXO_SID", "").strip()
EXO_API_TOKEN = os.getenv("EXO_API_TOKEN", "").strip()
EXO_WHATSAPP_FROM = os.getenv("EXO_WHATSAPP_FROM", "").strip()
EXO_API_PASSWORD = os.getenv("EXO_API_PASSWORD", "").strip()
EXOTEL_WHATSAPP_API_BASE = os.getenv(
    "EXOTEL_WHATSAPP_API_BASE",
    "https://api.in.exotel.com/v2/accounts"
).strip()

# =========================================================
# DB (PERSISTENT ON RENDER DISK)
# =========================================================
DEFAULT_DB_PATH = "var/data/team_inbox.db" if os.path.isdir("var/data") else "inbox.db"
DB_PATH = os.getenv("DB_URL", DEFAULT_DB_PATH).strip()


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True) if os.path.dirname(DB_PATH) else None

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS inbox_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wa_from TEXT,
            wa_to TEXT,
            direction TEXT,
            message_text TEXT,
            callback_type TEXT,
            created_at TEXT,
            agent_name TEXT DEFAULT ''
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_inbox_messages_wa_from
        ON inbox_messages(wa_from)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_inbox_messages_created_at
        ON inbox_messages(created_at)
    """)

    conn.commit()
    conn.close()


@app.on_event("startup")
def startup_event():
    init_db()


# =========================================================
# HELPERS
# =========================================================
def now_ist_string():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_inbound(payload: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Expected Exotel inbound payload shape:
    {
      "whatsapp": {
        "messages": [
          {
            "callback_type": "incoming_message",
            "from": "+9198XXXXXXX",
            "to": "+9179XXXXXXX",
            "timestamp": "...",
            "content": {
              "type": "text",
              "text": {
                "body": "Hi"
              }
            }
          }
        ]
      }
    }
    """
    try:
        messages = payload.get("whatsapp", {}).get("messages", [])
        if not messages:
            print("[DBG] No whatsapp.messages found")
            return None, None, None

        msg = messages[0]
        callback_type = (msg.get("callback_type") or "").strip()
        print("[DBG] callback_type:", callback_type)

        if callback_type != "incoming_message":
            print("[DBG] Not an incoming_message callback")
            return None, None, None

        wa_from = (msg.get("from") or "").strip()
        wa_to = (msg.get("to") or "").strip()

        content = msg.get("content", {}) or {}
        ctype = (content.get("type") or "").strip()

        text_body = ""
        if ctype == "text":
            text_body = ((content.get("text") or {}).get("body") or "").strip()

        print("[DBG] wa_from:", wa_from)
        print("[DBG] wa_to:", wa_to)
        print("[DBG] text_body:", text_body)

        return wa_from, wa_to, text_body

    except Exception as e:
        print("[ERR] parse_inbound error:", str(e))
        return None, None, None


def save_message(
    wa_from: str,
    wa_to: str,
    direction: str,
    message_text: str,
    callback_type: str = "",
    agent_name: str = "",
):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO inbox_messages
            (wa_from, wa_to, direction, message_text, callback_type, created_at, agent_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            wa_from or "",
            wa_to or "",
            direction or "",
            message_text or "",
            callback_type or "",
            now_ist_string(),
            agent_name or "",
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print("[ERR] save_message error:", str(e))


def send_text(to: str, text: str):
    if not EXO_SID or not EXO_API_TOKEN or not EXO_WHATSAPP_FROM:
        msg = "Missing EXO_SID / EXO_API_TOKEN / EXO_WHATSAPP_FROM"
        print("[ERR]", msg)
        return {"ok": False, "error": msg}

    url = f"{EXOTEL_WHATSAPP_API_BASE}/{EXO_SID}/messages"

    payload = {
        "whatsapp": {
            "messages": [
                {
                    "from": EXO_WHATSAPP_FROM,
                    "to": to,
                    "content": {
                        "type": "text",
                        "text": {
                            "body": text
                        }
                    }
                }
            ]
        }
    }

    print("[DBG] EXOTEL OUTBOUND URL:", url)
    print("[DBG] EXOTEL OUTBOUND PAYLOAD:", json.dumps(payload, indent=2))

    try:
        r = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            #auth=(EXO_SID, EXO_API_PASSWORD),
            auth=(EXO_API_KEY, EXO_API_TOKEN),
            timeout=30,
        )
        print("[DBG] EXOTEL RESPONSE STATUS:", r.status_code)
        print("[DBG] EXOTEL RESPONSE BODY:", r.text)
        return {
            "ok": r.ok,
            "status_code": r.status_code,
            "body": r.text,
        }
    except Exception as e:
        print("[ERR] send_text exception:", str(e))
        return {"ok": False, "error": str(e)}


def get_conversations():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            wa_from,
            MAX(created_at) AS last_message_at,
            COUNT(*) AS total_messages,
            SUM(CASE WHEN direction='inbound' THEN 1 ELSE 0 END) AS inbound_count,
            SUM(CASE WHEN direction='outbound' THEN 1 ELSE 0 END) AS outbound_count,
            (
                SELECT message_text
                FROM inbox_messages m2
                WHERE m2.wa_from = m1.wa_from
                ORDER BY m2.id DESC
                LIMIT 1
            ) AS last_message_text
        FROM inbox_messages m1
        GROUP BY wa_from
        ORDER BY last_message_at DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_messages_for_number(phone: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM inbox_messages
        WHERE wa_from = ?
        ORDER BY id ASC
    """, (phone,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_dashboard_stats():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS cnt FROM inbox_messages")
    total_messages = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(DISTINCT wa_from) AS cnt FROM inbox_messages")
    total_contacts = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) AS cnt FROM inbox_messages WHERE direction='inbound'")
    inbound_count = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) AS cnt FROM inbox_messages WHERE direction='outbound'")
    outbound_count = cur.fetchone()["cnt"]

    conn.close()

    return {
        "total_messages": total_messages,
        "total_contacts": total_contacts,
        "inbound_count": inbound_count,
        "outbound_count": outbound_count,
    }


# =========================================================
# ROUTES
# =========================================================
@app.get("/")
def root():
    return {"status": "ok", "service": "exotel-whatsapp-team-inbox", "db_path": DB_PATH}


@app.head("/")
def head_root():
    return Response(status_code=200)


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/env-check")
def env_check():
    return {
        "EXO_SID": EXO_SID,
        "EXO_API_TOKEN_present": bool(EXO_API_TOKEN),
        "EXO_API_TOKEN_last6": EXO_API_TOKEN[-6:] if EXO_API_TOKEN else "",
        "EXO_WHATSAPP_FROM": EXO_WHATSAPP_FROM,
        "EXOTEL_WHATSAPP_API_BASE": EXOTEL_WHATSAPP_API_BASE,
        "DB_PATH": DB_PATH,
    }


@app.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raw = await request.body()
        print("[ERR] JSON parse failed. RAW BODY:", raw.decode("utf-8", errors="ignore"))
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    print("[DBG] FULL PAYLOAD:", json.dumps(payload, indent=2))

    wa_from, wa_to, body = parse_inbound(payload)
    if not wa_from or not body:
        print("[DBG] No valid inbound message extracted; returning 200")
        return {"ok": True, "note": "No inbound text message parsed"}

    save_message(
        wa_from=wa_from,
        wa_to=wa_to,
        direction="inbound",
        message_text=body,
        callback_type="incoming_message",
    )

    incoming = body.strip().lower()

    if incoming in ["hi", "hello", "hey", "start", "menu"]:
        reply_text = (
            "Hello 👋\n\n"
            "Thank you for contacting us.\n"
            "Your message has been received successfully.\n\n"
            "Please let us know how we may assist you."
        )
    else:
        reply_text = (
            "Thank you for your message.\n\n"
            f"We have received: \"{body}\"\n\n"
            "We will respond as soon as possible."
        )

    send_result = send_text(wa_from, reply_text)

    if send_result.get("ok"):
        save_message(
            wa_from=wa_from,
            wa_to=wa_to,
            direction="outbound",
            message_text=reply_text,
            callback_type="outbound_reply",
            agent_name="Bot",
        )

    return {
        "ok": True,
        "wa_from": wa_from,
        "wa_to": wa_to,
        "body": body,
        "send_result": send_result,
    }


@app.post("/send-test")
async def send_test(request: Request):
    """
    Manual test endpoint.
    POST JSON:
    {
      "to": "+9198XXXXXXXX",
      "text": "Hello from Exotel test"
    }
    """
    payload = await request.json()
    to = (payload.get("to") or "").strip()
    text = (payload.get("text") or "Hello from Exotel test").strip()

    if not to:
        return JSONResponse({"ok": False, "error": "'to' is required"}, status_code=400)

    result = send_text(to, text)
    return {"ok": True, "result": result}


@app.post("/team-inbox/reply")
async def team_inbox_reply(request: Request):
    payload = await request.json()
    to = (payload.get("to") or "").strip()
    text = (payload.get("text") or "").strip()
    agent_name = (payload.get("agent_name") or "Agent").strip()

    if not to:
        return JSONResponse({"ok": False, "error": "'to' is required"}, status_code=400)

    if not text:
        return JSONResponse({"ok": False, "error": "'text' is required"}, status_code=400)

    result = send_text(to, text)

    if result.get("ok"):
        save_message(
            wa_from=to,
            wa_to=EXO_WHATSAPP_FROM,
            direction="outbound",
            message_text=text,
            callback_type="agent_reply",
            agent_name=agent_name,
        )

    return {"ok": True, "result": result}


@app.get("/team-inbox", response_class=HTMLResponse)
def team_inbox(phone: Optional[str] = None):
    stats = get_dashboard_stats()
    conversations = get_conversations()
    selected_phone = (phone or "").strip()
    messages = get_messages_for_number(selected_phone) if selected_phone else []

    conversation_html = ""
    for row in conversations:
        wa_from = row["wa_from"] or ""
        last_message = escape((row["last_message_text"] or "")[:100])
        is_active = wa_from == selected_phone
        conversation_html += f"""
        <a href="/team-inbox?phone={quote(wa_from)}" class="conversation-link">
            <div class="conversation-card {'active' if is_active else ''}">
                <div class="conversation-top">
                    <div class="conversation-name">{escape(wa_from)}</div>
                    <div class="conversation-time">{escape(row["last_message_at"] or "")}</div>
                </div>
                <div class="conversation-preview">{last_message}</div>
                <div class="conversation-meta">
                    <span>Total: {row['total_messages']}</span>
                    <span>Inbound: {row['inbound_count']}</span>
                    <span>Outbound: {row['outbound_count']}</span>
                </div>
            </div>
        </a>
        """

    messages_html = ""
    for row in messages:
        direction = row["direction"] or ""
        bubble_class = "bubble-outbound" if direction == "outbound" else "bubble-inbound"
        row_class = "message-row outbound" if direction == "outbound" else "message-row inbound"
        label = "Customer" if direction == "inbound" else (row["agent_name"] or "Bot")
        messages_html += f"""
        <div class="{row_class}">
            <div class="message-bubble {bubble_class}">
                <div class="message-label">{escape(label)}</div>
                <div class="message-text">{escape(row['message_text'] or '')}</div>
                <div class="message-time">{escape(row['created_at'] or '')}</div>
            </div>
        </div>
        """

    reply_panel = ""
    if selected_phone:
        reply_panel = f"""
        <div class="reply-panel">
            <div class="reply-title">Reply to {escape(selected_phone)}</div>
            <form onsubmit="sendReply(event)" class="reply-form">
                <input type="hidden" id="reply_to" value="{escape(selected_phone)}" />
                <div class="input-group">
                    <label class="input-label">Agent Name</label>
                    <input type="text" id="agent_name" class="text-input" placeholder="Agent name" value="Admin" />
                </div>
                <div class="input-group">
                    <label class="input-label">Message</label>
                    <textarea id="reply_text" class="text-area" placeholder="Type your reply here..."></textarea>
                </div>
                <div class="reply-actions">
                    <button type="submit" class="send-button">Send Reply</button>
                    <div id="reply_status" class="reply-status"></div>
                </div>
            </form>
        </div>
        """

    html = f"""
    <!doctype html>
    <html>
    <head>
        <title>WhatsApp Team Inbox</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <style>
            * {{
                box-sizing: border-box;
            }}

            body {{
                margin: 0;
                font-family: Inter, Arial, sans-serif;
                background: #f3f6fb;
                color: #142033;
            }}

            .app-shell {{
                min-height: 100vh;
                display: flex;
                flex-direction: column;
            }}

            .topbar {{
                background: linear-gradient(135deg, #0f172a, #1d4ed8);
                color: white;
                padding: 20px 24px;
                box-shadow: 0 6px 24px rgba(17, 24, 39, 0.18);
            }}

            .topbar-title {{
                font-size: 24px;
                font-weight: 700;
                margin-bottom: 12px;
            }}

            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(4, minmax(120px, 1fr));
                gap: 12px;
            }}

            .stat-card {{
                background: rgba(255, 255, 255, 0.14);
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 16px;
                padding: 14px 16px;
                backdrop-filter: blur(6px);
            }}

            .stat-label {{
                font-size: 12px;
                opacity: 0.86;
                margin-bottom: 6px;
            }}

            .stat-value {{
                font-size: 24px;
                font-weight: 700;
            }}

            .main-layout {{
                display: grid;
                grid-template-columns: 360px 1fr;
                height: calc(100vh - 136px);
            }}

            .sidebar {{
                background: #ffffff;
                border-right: 1px solid #e4eaf4;
                overflow-y: auto;
            }}

            .sidebar-header {{
                padding: 18px 18px 12px;
                font-weight: 700;
                font-size: 16px;
                position: sticky;
                top: 0;
                background: #ffffff;
                border-bottom: 1px solid #eef2f7;
                z-index: 5;
            }}

            .conversation-link {{
                text-decoration: none;
                color: inherit;
                display: block;
                padding: 0 12px 12px;
            }}

            .conversation-card {{
                background: #ffffff;
                border: 1px solid #e8edf5;
                border-radius: 16px;
                padding: 14px;
                transition: all 0.2s ease;
                box-shadow: 0 4px 14px rgba(15, 23, 42, 0.04);
            }}

            .conversation-card:hover {{
                transform: translateY(-1px);
                box-shadow: 0 8px 20px rgba(15, 23, 42, 0.08);
                border-color: #cdd9ea;
            }}

            .conversation-card.active {{
                background: #eff6ff;
                border-color: #93c5fd;
                box-shadow: 0 10px 22px rgba(37, 99, 235, 0.12);
            }}

            .conversation-top {{
                display: flex;
                justify-content: space-between;
                gap: 12px;
                align-items: flex-start;
            }}

            .conversation-name {{
                font-weight: 700;
                font-size: 15px;
                color: #0f172a;
                word-break: break-word;
            }}

            .conversation-time {{
                font-size: 11px;
                color: #64748b;
                white-space: nowrap;
            }}

            .conversation-preview {{
                font-size: 13px;
                color: #475569;
                line-height: 1.45;
                margin-top: 8px;
                min-height: 36px;
            }}

            .conversation-meta {{
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
                font-size: 11px;
                color: #64748b;
                margin-top: 10px;
            }}

            .chat-panel {{
                display: flex;
                flex-direction: column;
                min-width: 0;
            }}

            .chat-header {{
                background: #ffffff;
                border-bottom: 1px solid #e5ebf5;
                padding: 18px 22px;
                font-size: 18px;
                font-weight: 700;
                box-shadow: 0 1px 0 rgba(15, 23, 42, 0.03);
            }}

            .chat-body {{
                flex: 1;
                overflow-y: auto;
                padding: 24px;
                background:
                    radial-gradient(circle at top left, rgba(59, 130, 246, 0.08), transparent 24%),
                    linear-gradient(180deg, #f8fbff 0%, #eef4fb 100%);
            }}

            .message-row {{
                display: flex;
                margin-bottom: 14px;
            }}

            .message-row.inbound {{
                justify-content: flex-start;
            }}

            .message-row.outbound {{
                justify-content: flex-end;
            }}

            .message-bubble {{
                max-width: 75%;
                border-radius: 18px;
                padding: 12px 14px;
                box-shadow: 0 6px 18px rgba(15, 23, 42, 0.08);
                border: 1px solid rgba(15, 23, 42, 0.05);
            }}

            .bubble-inbound {{
                background: #ffffff;
                color: #0f172a;
            }}

            .bubble-outbound {{
                background: #dcfce7;
                color: #14532d;
            }}

            .message-label {{
                font-size: 12px;
                font-weight: 700;
                margin-bottom: 6px;
                opacity: 0.85;
            }}

            .message-text {{
                white-space: pre-wrap;
                line-height: 1.52;
                font-size: 14px;
                word-break: break-word;
            }}

            .message-time {{
                font-size: 11px;
                margin-top: 8px;
                opacity: 0.68;
            }}

            .reply-panel {{
                background: #ffffff;
                border-top: 1px solid #e5ebf5;
                padding: 18px 22px 22px;
                box-shadow: 0 -8px 24px rgba(15, 23, 42, 0.04);
            }}

            .reply-title {{
                font-size: 16px;
                font-weight: 700;
                margin-bottom: 14px;
            }}

            .reply-form {{
                display: grid;
                gap: 12px;
            }}

            .input-group {{
                display: grid;
                gap: 6px;
            }}

            .input-label {{
                font-size: 13px;
                font-weight: 600;
                color: #334155;
            }}

            .text-input,
            .text-area {{
                width: 100%;
                border: 1px solid #d7e0ec;
                border-radius: 12px;
                background: #f8fbff;
                padding: 12px 14px;
                font-size: 14px;
                outline: none;
                transition: border-color 0.2s ease, box-shadow 0.2s ease;
            }}

            .text-input:focus,
            .text-area:focus {{
                border-color: #60a5fa;
                box-shadow: 0 0 0 4px rgba(96, 165, 250, 0.18);
            }}

            .text-area {{
                min-height: 120px;
                resize: vertical;
            }}

            .reply-actions {{
                display: flex;
                align-items: center;
                gap: 14px;
                flex-wrap: wrap;
            }}

            .send-button {{
                background: linear-gradient(135deg, #16a34a, #15803d);
                color: white;
                border: none;
                border-radius: 12px;
                padding: 12px 18px;
                font-size: 14px;
                font-weight: 700;
                cursor: pointer;
                box-shadow: 0 10px 18px rgba(22, 163, 74, 0.18);
                transition: transform 0.2s ease, box-shadow 0.2s ease;
            }}

            .send-button:hover {{
                transform: translateY(-1px);
                box-shadow: 0 14px 24px rgba(22, 163, 74, 0.24);
            }}

            .reply-status {{
                font-size: 13px;
                color: #475569;
            }}

            .empty-state {{
                color: #64748b;
                font-size: 14px;
                background: rgba(255, 255, 255, 0.7);
                border: 1px dashed #cbd5e1;
                border-radius: 16px;
                padding: 20px;
            }}

            @media (max-width: 900px) {{
                .stats-grid {{
                    grid-template-columns: repeat(2, minmax(120px, 1fr));
                }}

                .main-layout {{
                    grid-template-columns: 1fr;
                    height: auto;
                }}

                .sidebar {{
                    max-height: 38vh;
                    border-right: none;
                    border-bottom: 1px solid #e4eaf4;
                }}

                .chat-panel {{
                    min-height: 62vh;
                }}

                .message-bubble {{
                    max-width: 90%;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="app-shell">
            <div class="topbar">
                <div class="topbar-title">WhatsApp Team Inbox</div>
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-label">Total Messages</div>
                        <div class="stat-value">{stats['total_messages']}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Total Contacts</div>
                        <div class="stat-value">{stats['total_contacts']}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Inbound</div>
                        <div class="stat-value">{stats['inbound_count']}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Outbound</div>
                        <div class="stat-value">{stats['outbound_count']}</div>
                    </div>
                </div>
            </div>

            <div class="main-layout">
                <div class="sidebar">
                    <div class="sidebar-header">Conversations</div>
                    {conversation_html or '<div style="padding:12px;"><div class="empty-state">No conversations yet.</div></div>'}
                </div>

                <div class="chat-panel">
                    <div class="chat-header">{escape(selected_phone) if selected_phone else 'Select a conversation'}</div>
                    <div class="chat-body">
                        {messages_html or '<div class="empty-state">No messages to display.</div>'}
                    </div>
                    {reply_panel if selected_phone else ''}
                </div>
            </div>
        </div>

        <script>
        async function sendReply(event) {{
            event.preventDefault();

            const to = document.getElementById("reply_to").value;
            const text = document.getElementById("reply_text").value;
            const agent_name = document.getElementById("agent_name").value;
            const status = document.getElementById("reply_status");

            if (!text.trim()) {{
                status.innerText = "Please enter a message.";
                return;
            }}

            status.innerText = "Sending...";

            try {{
                const resp = await fetch("/team-inbox/reply", {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{ to, text, agent_name }})
                }});

                const data = await resp.json();

                if (data.ok && data.result && data.result.ok) {{
                    status.innerText = "Reply sent successfully.";
                    document.getElementById("reply_text").value = "";
                    setTimeout(() => window.location.reload(), 600);
                }} else {{
                    status.innerText = "Failed to send reply.";
                }}
            }} catch (e) {{
                status.innerText = "Unexpected error while sending reply.";
            }}
        }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
