import os
import json
from typing import Optional, Tuple

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

app = FastAPI()

# =========================================================
# ENV
# =========================================================
EXO_API_KEY = os.getenv("EXO_API_KEY", "").strip()
EXO_SID = os.getenv("EXO_SID", "").strip()
EXO_API_TOKEN = os.getenv("EXO_API_TOKEN", "").strip()
EXO_WHATSAPP_FROM = os.getenv("EXO_WHATSAPP_FROM", "").strip()
EXOTEL_WHATSAPP_API_BASE = os.getenv(
    "EXOTEL_WHATSAPP_API_BASE",
    "https://api.exotel.com/v2/accounts"
).strip()

# =========================================================
# HELPERS
# =========================================================
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


def send_text(to: str, text: str):
    if not EXO_API_KEY or not EXO_API_TOKEN or not EXO_SID or not EXO_WHATSAPP_FROM:
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


# =========================================================
# ROUTES
# =========================================================
@app.get("/")
def root():
    return {"status": "ok", "service": "exotel-whatsapp-test"}

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

    incoming = body.strip().lower()

    if incoming in ["hi", "hello", "hey", "start", "menu"]:
        reply_text = (
            "Hello 👋\n\n"
            "Exotel WhatsApp test is working.\n\n"
            f"You sent: {body}"
        )
    else:
        reply_text = f"Received your message: {body}"

    send_result = send_text(wa_from, reply_text)
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