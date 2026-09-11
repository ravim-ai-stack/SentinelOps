"""Combines every Catalog explorer panel router into one, mounted by main.py."""

from fastapi import APIRouter

from catalog_explorer import access, stats, tree

router = APIRouter(tags=["catalog-explorer"])
router.include_router(stats.router)
router.include_router(tree.router)
router.include_router(access.router)
