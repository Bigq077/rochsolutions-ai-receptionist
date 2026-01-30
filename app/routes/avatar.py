from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/avatar", tags=["avatar"])

class AvatarChatRequest(BaseModel):
    clinic_id: str
    session_id: str
    message: str

class AvatarChatResponse(BaseModel):
    reply_text: str
    session_id: str


@router.post("/chat", response_model=AvatarChatResponse)
async def avatar_chat(payload: AvatarChatRequest):
    """
    MVP endpoint:
    - accepts text from a web/avatar UI
    - returns the AI receptionist's text response

    For now, this uses a stub response.
    Next step: connect to your existing AI brain function used by Twilio.
    """
    # Basic validation
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty")

    # ✅ TEMP STUB (so you can test immediately)
    reply = f"(avatar stub) I received: {payload.message}"

    # TODO: Replace stub with your existing AI logic
    # reply = await your_existing_ai_function(
    #     clinic_id=payload.clinic_id,
    #     session_id=payload.session_id,
    #     user_text=payload.message,
    #     channel="avatar_web"
    # )

    return AvatarChatResponse(reply_text=reply, session_id=payload.session_id)
