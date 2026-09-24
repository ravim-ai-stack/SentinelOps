"""Catalog explorer page — the 'Catalogs' tree panel (search supported)."""

from typing import Optional

from fastapi import APIRouter, Query

from catalog_service import build_full_tree, exclude_system_catalogs, filter_tree, list_distinct_tags

router = APIRouter()


@router.get("/api/catalog/tree")
def catalog_tree(
    search: Optional[str] = Query(None, description="Filter by catalog/schema/object name"),
    tag: Optional[str] = Query(None, description="Filter to objects carrying this Unity Catalog tag"),
):
    return {"catalogs": filter_tree(exclude_system_catalogs(build_full_tree()), search, tag)}


@router.get("/api/catalog/tags")
def catalog_tags():
    """Distinct Unity Catalog tags across all tables, for the tag filter dropdown."""
    return {"tags": list_distinct_tags(exclude_system_catalogs(build_full_tree()))}
