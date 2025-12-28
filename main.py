import os
import requests
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "gourav_wa_verify_2025")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")

GRAPH_VERSION = "v19.0"

app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}

# Webhook verification
@app.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    qp = request.query_params
    mode = qp.get("hub.mode")
    token = qp.get("hub.verify_token")
    challenge = qp.get("hub.challenge")

    # Nice behavior if opened in browser without params
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
        "text": {"body": text},
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    print("Send message status:", r.status_code, r.text)

# Incoming events
@app.post("/webhook/whatsapp")
async def receive_webhook(request: Request):
    payload = await request.json()
    print("Incoming webhook:", payload)

    # Try to extract sender + text (handles common Cloud API structure)
    try:
        changes = payload["entry"][0]["changes"][0]["value"]
        msgs = changes.get("messages", [])
        if msgs:
            msg = msgs[0]
            from_number = msg.get("from")  # this is the user's WhatsApp number (digits)
            text_body = (msg.get("text") or {}).get("body", "")
            if from_number:
                reply = f"✅ Got your message: {text_body}"
                send_text(from_number, reply)
    except Exception as e:
        print("Parse error:", e)

    return JSONResponse({"status": "ok"})
