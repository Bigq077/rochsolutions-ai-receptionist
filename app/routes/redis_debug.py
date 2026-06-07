from fastapi import APIRouter
from app.storage.redis_store import redis_client

router = APIRouter()

@router.get("/debug/redis")
async def debug_redis():
    if not redis_client:
        return {"ok": False, "error": "redis_client is None (REDIS_URL missing or not loaded)"}
    try:
        await redis_client.set("redis_ping_test", "1", ex=60)
        v = await redis_client.get("redis_ping_test")
        return {"ok": True, "value": v}
    except Exception as e:
        return {"ok": False, "error": str(e)}
