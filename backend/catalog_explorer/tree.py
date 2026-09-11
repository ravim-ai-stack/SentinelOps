"""Catalog explorer page — the 'Catalogs' tree panel (search supported)."""

from typing import Optional

from fastapi import APIRouter, Query

from catalog_service import build_full_tree, filter_tree

router = APIRouter()


@router.get("/api/catalog/tree")
def catalog_tree(search: Optional[str] = Query(None, description="Filter by catalog/schema/object name")):
    return {"catalogs": filter_tree(build_full_tree(), search)}
