"""Data security page — 'Recent classifications' table."""

from fastapi import APIRouter

from catalog_service import fetch_tables_and_views
from security_service import build_pii_columns

router = APIRouter()


@router.get("/api/security/recent-classifications")
def recent_classifications():
    pii_columns = build_pii_columns()
    table_meta = {
        f"{t['catalog']}.{t['schema']}.{t['name']}": t
        for t in fetch_tables_and_views()
    }
    recent = sorted(
        pii_columns, key=lambda c: table_meta.get(c["table_path"], {}).get("last_altered") or "", reverse=True
    )[:8]
    for c in recent:
        c["last_altered"] = table_meta.get(c["table_path"], {}).get("last_altered")
    return {"recent_classifications": recent}
