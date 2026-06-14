"""Adversary LLM diagnostics endpoint (ops aid for the Gemini narrative)."""
from fastapi import APIRouter

from app.agents.adversary import Adversary

router = APIRouter(prefix="/api/v1/adversary", tags=["adversary"])


@router.get("/diagnostics")
async def adversary_diagnostics() -> dict:
    return Adversary().diagnostics()
