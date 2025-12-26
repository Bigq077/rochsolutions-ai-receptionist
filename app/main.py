from fastapi import FastAPI

from app.routes.twilio import router as twilio_router
from app.routes.google_calendar import router as google_calendar_router

app = FastAPI()

# Routers
app.include_router(twilio_router)
app.include_router(google_calendar_router)

# Health check
@app.get("/")
def health():
    return {"status": "ok", "service": "rochsolutions-ai-receptionist"}
