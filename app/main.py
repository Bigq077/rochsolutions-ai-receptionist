# main.py

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.twilio import router as twilio_router
from app.routes.google_calendar import router as google_calendar_router
from app.routes.redis_debug import router as redis_debug_router
from app.routes.avatar import router as avatar_router

app = FastAPI()

# ✅ CORS — REQUIRED for browser (Netlify) → Render calls
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # Netlify production domain (THIS was missing)
        "https://lucent-mousse-6a9947.netlify.app",

        # Netlify preview domain (safe to keep)
        "https://697ca6ae7ddd451e33f646ce--lucent-mousse-6a9947.netlify.app",

        # Local dev
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health endpoints
@app.get("/health")
def health():
    return {"ok": True, "service": "rochsolutions-ai-receptionist"}

@app.get("/")
def root():
    return {"status": "ok"}

# Routers
app.include_router(twilio_router)
app.include_router(google_calendar_router)
app.include_router(redis_debug_router)
app.include_router(avatar_router)
