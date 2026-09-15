"""Access governance page — Grants tab (raw Unity Catalog privilege grants)."""

from fastapi import APIRouter

from grants_service import fetch_grants

router = APIRouter()


@router.get("/api/access/grants")
def access_grants():
    return {"grants": fetch_grants()}
