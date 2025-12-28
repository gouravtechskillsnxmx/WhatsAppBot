import os
from typing import Dict, List

import requests
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from openai import OpenAI


VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "gourav_wa_verify_2025")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

GRAPH_VERSION = "v19.0"

client = OpenAI(api_key=OPENAI_API_KEY)
app = FastAPI()

# Simple in-memory conversation store (resets on redeploy)
MEMORY: Dict[str, List[dict]] = {}

SYSTEM_PROMPT = (
    "You are a helpful WhatsApp assistant for BeyondBGCMNxMx. "
    "Be concise, friendly, and ask one question at a time when needed."
)

@app.get("/")
def health():
    return {"status": "ok"}

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

def send_text(to: str, text: str):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print("Missing WHATSAPP_TOKEN or PHONE_NUMBER_ID")
        return

    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text[:4096]},  # WhatsApp text limit safety
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    print("Send message status:", r.status_code, r.text)

def get_ai_reply(user_id: str, user_text: str) -> str:
    # Initialize memory
    if user_id not in MEMORY:
        MEMORY[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add user message
    MEMORY[user_id].append({"role": "user", "content": user_text})

    # Keep history short to control cost
    MEMORY[user_id] = MEMORY[user_id][-12:]  # last ~12 messages including system

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=MEMORY[user_id],
        temperature=0.3,
    )
    reply = resp.choices[0].message.content.strip()

    # Add assistant message back to memory
    MEMORY[user_id].append({"role": "assistant", "content": reply})
    MEMORY[user_id] = MEMORY[user_id][-12:]

    return reply

@app.post("/webhook/whatsapp")
async def receive_webhook(request: Request):
    payload = await request.json()
    print("Incoming webhook:", payload)

    try:
        changes = payload["entry"][0]["changes"][0]["value"]
        messages = changes.get("messages", [])
        if not messages:
            return JSONResponse({"status": "ok"})  # could be statuses, etc.

        msg = messages[0]
        from_number = msg.get("from")  # user's WhatsApp number (digits)
        text_body = (msg.get("text") or {}).get("body", "").strip()

        # Ignore non-text for now
        if not from_number or not text_body:
            return JSONResponse({"status": "ok"})

        # AI reply
        ai_text = get_ai_reply(from_number, text_body)
        send_text(from_number, ai_text)

    except Exception as e:
        print("Webhook parse/error:", e)

    return JSONResponse({"status": "ok"})
