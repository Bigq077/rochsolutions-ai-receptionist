from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI

from app.routes.twilio import router as twilio_router
from app.routes.google_calendar import router as google_calendar_router

app = FastAPI()

from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True}

# Routers
app.include_router(twilio_router)
app.include_router(google_calendar_router)

# Health check
@app.get("/")
def health():
    return {"status": "ok", "service": "rochsolutions-ai-receptionist"}

from app.routes.redis_debug import router as redis_debug_router
app.include_router(redis_debug_router)

