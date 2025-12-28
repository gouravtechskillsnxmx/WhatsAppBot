
import os
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "gourav_wa_verify_2025")

app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}

# Meta webhook verification (GET)
@app.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    qp = request.query_params
    mode = qp.get("hub.mode")
    token = qp.get("hub.verify_token")
    challenge = qp.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return PlainTextResponse(challenge)

    return PlainTextResponse("Verification failed", status_code=403)

# Incoming WhatsApp events (POST)
@app.post("/webhook/whatsapp")
async def receive_webhook(request: Request):
    payload = await request.json()
    print("Incoming webhook:", payload)
    return JSONResponse({"status": "ok"})
