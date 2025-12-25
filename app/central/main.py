from fastapi import FastAPI
from app.routes.twilio import router as twilio_router

app = FastAPI()
app.include_router(twilio_router)

@app.get("/")
def health():
    return {"status": "ok", "service": "rochsolutions-ai-receptionist"}


