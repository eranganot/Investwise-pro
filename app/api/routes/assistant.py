"""AI assistant + digest endpoints (Phase H)."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.services.ask_service import answer
from app.services.digest_service import build as build_digest

router = APIRouter(prefix="/api/v1", tags=["assistant"])


class AskRequest(BaseModel):
    question: str = ""


@router.post("/ask")
async def ask(req: AskRequest, request: Request,
              session: AsyncSession = Depends(get_session),
              user: User = Depends(acting_user)) -> dict:
    result = await answer(session, user, req.question)
    posthog_client = getattr(request.app.state, "posthog_client", None)
    if posthog_client is not None:
        posthog_client.capture(
            "assistant_answered",
            properties={"question_length": len(req.question)},
        )
    return result


@router.get("/digest")
async def digest(session: AsyncSession = Depends(get_session),
                 user: User = Depends(acting_user)) -> dict:
    return await build_digest(session, user)
