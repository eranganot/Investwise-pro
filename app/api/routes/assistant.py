"""AI assistant + digest endpoints (Phase H)."""
from fastapi import APIRouter, Depends
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
async def ask(req: AskRequest, session: AsyncSession = Depends(get_session),
              user: User = Depends(acting_user)) -> dict:
    return await answer(session, user, req.question)


@router.get("/digest")
async def digest(session: AsyncSession = Depends(get_session),
                 user: User = Depends(acting_user)) -> dict:
    return await build_digest(session, user)
