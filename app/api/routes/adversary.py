"""Adversary LLM diagnostics endpoint (ops aid for the Gemini narrative)."""
from fastapi import APIRouter, Depends

from app.agents.adversary import Adversary
from app.core.auth import Role, require_role

router = APIRouter(prefix="/api/v1/adversary", tags=["adversary"])


@router.get("/diagnostics", dependencies=[Depends(require_role(Role.SUPERADMIN))])
async def adversary_diagnostics() -> dict:
    """SUPERADMIN (PR 110, S4): key presence + a live LLM call when enabled."""
    return Adversary().diagnostics()
