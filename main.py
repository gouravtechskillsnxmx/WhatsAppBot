from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

VERIFY_TOKEN = "gourav_wa_verify_2025"

app = FastAPI()

@app.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == VERIFY_TOKEN
    ):
        return PlainTextResponse(params.get("hub.challenge"))
    return PlainTextResponse("Verification failed", status_code=403)

@app.post("/webhook/whatsapp")
async def receive_message(request: Request):
    payload = await request.json()
    print(payload)  # WhatsApp message/event will be here
    return {"status": "ok"}
