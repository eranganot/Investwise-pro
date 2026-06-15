"""Commodity catalog endpoint."""
from fastapi import APIRouter

from app.services import commodities as cat

router = APIRouter(prefix="/api/v1", tags=["commodities"])


@router.get("/commodities")
async def commodities() -> dict:
    return {"categories": cat.CATEGORY_ORDER, "by_category": cat.by_category(),
            "not_investable": cat.NOT_INVESTABLE}
