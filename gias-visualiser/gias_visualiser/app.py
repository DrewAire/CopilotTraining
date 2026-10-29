from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .data import (
    DB_PATH,
    fetch_all_schools,
    fetch_filters,
    fetch_group_detail,
    fetch_school_detail,
    search_records,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="GIAS Visualiser")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.on_event("startup")
    async def startup_event() -> None:
        if not DB_PATH.exists():
            from .data import ensure_database

            ensure_database()

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        with sqlite3.connect(DB_PATH) as conn:
            school_count = conn.execute("SELECT COUNT(*) FROM schools").fetchone()[0]
            group_count = conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        return {"ok": True, "schools": school_count, "groups": group_count}

    @app.get("/api/filters")
    async def filters() -> dict[str, list[str]]:
        return fetch_filters(DB_PATH)

    @app.get("/api/schools")
    async def schools(
        status: str | None = None,
        phase: str | None = None,
        type_name: str | None = None,
        group_uid: str | None = None,
    ) -> list[dict[str, object]]:
        return fetch_all_schools(
            DB_PATH,
            status=status,
            phase=phase,
            type_name=type_name,
            group_uid=group_uid,
        )

    @app.get("/api/search")
    async def search(
        mode: str = "school", q: str = "", limit: int = 20
    ) -> list[dict[str, object]]:
        return search_records(q, mode=mode, db_path=DB_PATH, limit=limit)

    @app.get("/api/groups/{group_uid}")
    async def group_detail(group_uid: str) -> dict[str, object]:
        item = fetch_group_detail(group_uid, DB_PATH)
        if item is None:
            raise HTTPException(status_code=404, detail="Group not found")
        return item

    @app.get("/api/schools/{urn}")
    async def school_detail(urn: str) -> dict[str, object]:
        item = fetch_school_detail(urn, DB_PATH)
        if item is None:
            raise HTTPException(status_code=404, detail="School not found")
        return item

    @app.get("/api/meta")
    async def meta() -> dict[str, object]:
        with sqlite3.connect(DB_PATH) as conn:
            total = conn.execute("SELECT COUNT(*) FROM schools").fetchone()[0]
            mapped = conn.execute(
                "SELECT COUNT(*) FROM schools WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
            ).fetchone()[0]
        return {"total_schools": total, "mapped_schools": mapped}

    return app
