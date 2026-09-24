"""Catalog explorer page — the 6 stat cards (catalogs/schemas/tables/
functions/volumes/models)."""

from fastapi import APIRouter

from catalog_service import build_full_tree, summarize

router = APIRouter()


@router.get("/api/catalog/stats")
def catalog_stats():
    return summarize(build_full_tree())
