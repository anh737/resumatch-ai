from fastapi import APIRouter

from handler.chat import submit_chat
from schemas.chat import ChatAccepted, ChatRequest

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatAccepted, status_code=202)
async def chat(request: ChatRequest) -> ChatAccepted:
    return await submit_chat(request)
