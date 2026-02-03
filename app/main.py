# main.py

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.twilio import router as twilio_router
from app.routes.google_calendar import router as google_calendar_router
from app.routes.redis_debug import router as redis_debug_router
from app.routes.avatar import router as avatar_router

# Admin route (temporary, for clearing google_tokens)
from app.routes.admin import router as admin_router

app = FastAPI()

# ✅ CORS — allow ALL Netlify domains + local dev
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https://.*\.netlify\.app$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------
# Health endpoints
# --------------------
@app.get("/health")
def health():
    return {"ok": True, "service": "rochsolutions-ai-receptionist"}

@app.get("/")
def root():
    return {"status": "ok"}

# --------------------
# Routers
# --------------------
app.include_router(twilio_router)
app.include_router(google_calendar_router)
app.include_router(redis_debug_router)
app.include_router(avatar_router)
app.include_router(admin_router)  # temporary admin router
