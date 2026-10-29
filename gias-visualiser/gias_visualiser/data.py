from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from filelock import FileLock
from pyproj import Transformer

BASE_URL = "https://ea-edubase-api-prod.azurewebsites.net/edubase/downloads/public/"
PREFIXES = [
    "edubasealldata",
    "allgroupsdata",
    "alllinksdata",
    "links_edubasealldata",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "gias.sqlite3"

BNG_TO_WGS84 = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)


def ensure_directories() -> tuple[Path, Path]:
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    return DOWNLOADS_DIR, DATA_DIR


def _existing_snapshot_files_for(
    date_string: str, download_dir: Path | None = None
) -> dict[str, Path]:
    download_dir = download_dir or DOWNLOADS_DIR
    files: dict[str, Path] = {}
    for prefix in PREFIXES:
        candidate = download_dir / f"{prefix}{date_string}.csv"
        if candidate.exists():
            files[prefix] = candidate
    return files


def _find_latest_available_snapshot(download_dir: Path | None = None) -> str | None:
    download_dir = download_dir or DOWNLOADS_DIR
    dates: set[str] = set()
    for pattern in PREFIXES:
        for path in download_dir.glob(f"{pattern}*.csv"):
            suffix = path.name.removeprefix(pattern)
            if suffix.endswith(".csv"):
                suffix = suffix[:-4]
            if len(suffix) == 8 and suffix.isdigit():
                dates.add(suffix)
    if not dates:
        return None
    return max(sorted(dates))


def _download_file(
    prefix: str, snapshot_date: str, download_dir: Path | None = None
) -> Path:
    download_dir = download_dir or DOWNLOADS_DIR
    url = f"{BASE_URL}{prefix}{snapshot_date}.csv"
    file_path = download_dir / f"{prefix}{snapshot_date}.csv"
    with httpx.Client(timeout=30.0) as client:
        response = client.get(url)
        response.raise_for_status()
        file_path.write_bytes(response.content)
    return file_path


def _choose_snapshot_date(download_dir: Path | None = None) -> str:
    download_dir = download_dir or DOWNLOADS_DIR
    today = date.today().strftime("%Y%m%d")
    needed = _existing_snapshot_files_for(today, download_dir)
    if len(needed) == len(PREFIXES):
        return today

    for prefix in PREFIXES:
        if prefix not in needed:
            try:
                _download_file(prefix, today, download_dir)
            except httpx.HTTPError:
                continue
    complete_today = _existing_snapshot_files_for(today, download_dir)
    if len(complete_today) == len(PREFIXES):
        return today

    latest = _find_latest_available_snapshot(download_dir)
    if latest:
        return latest

    raise FileNotFoundError("No valid GIAS CSV snapshot was found or downloaded.")


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"n/a", "na", "not recorded", "not applicable"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _bng_to_latlng(easting: Any, northing: Any) -> tuple[float | None, float | None]:
    east = _coerce_float(easting)
    north = _coerce_float(northing)
    if east is None or north is None:
        return None, None
    try:
        lng, lat = BNG_TO_WGS84.transform(east, north)
        return lat, lng
    except Exception:
        return None, None


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        reader = csv.DictReader(io.StringIO(text, newline=""))
        return [
            {key: (value if value is not None else "") for key, value in row.items()}
            for row in reader
        ]
    raise UnicodeDecodeError(
        "utf-8", raw, 0, 1, "Unable to decode CSV with utf-8 or cp1252"
    )


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS school_links")
    conn.execute("DROP TABLE IF EXISTS memberships")
    conn.execute("DROP TABLE IF EXISTS groups")
    conn.execute("DROP TABLE IF EXISTS schools")
    conn.execute("""
        CREATE TABLE schools (
            urn TEXT PRIMARY KEY,
            name TEXT,
            la_code TEXT,
            la_name TEXT,
            establishment_number TEXT,
            status TEXT,
            status_code TEXT,
            type_name TEXT,
            type_code TEXT,
            phase_name TEXT,
            phase_code TEXT,
            town TEXT,
            county TEXT,
            postcode TEXT,
            latitude REAL,
            longitude REAL,
            raw_json TEXT
        )
        """)
    conn.execute("""
        CREATE TABLE groups (
            group_uid TEXT PRIMARY KEY,
            name TEXT,
            group_type TEXT,
            group_status TEXT,
            postcode TEXT,
            town TEXT,
            raw_json TEXT
        )
        """)
    conn.execute("""
        CREATE TABLE memberships (
            urn TEXT,
            group_uid TEXT,
            group_name TEXT,
            joined_date TEXT,
            establishment_name TEXT,
            raw_json TEXT
        )
        """)
    conn.execute("""
        CREATE TABLE school_links (
            urn TEXT,
            link_urn TEXT,
            link_name TEXT,
            link_type TEXT,
            raw_json TEXT
        )
        """)
    conn.execute("CREATE INDEX idx_schools_name ON schools(name)")
    conn.execute("CREATE INDEX idx_schools_status ON schools(status)")
    conn.execute("CREATE INDEX idx_schools_phase ON schools(phase_name)")
    conn.execute("CREATE INDEX idx_schools_type ON schools(type_name)")
    conn.execute("CREATE INDEX idx_groups_name ON groups(name)")
    conn.execute("CREATE INDEX idx_memberships_group ON memberships(group_uid)")
    conn.execute("CREATE INDEX idx_memberships_school ON memberships(urn)")


def import_snapshot(
    snapshot_date: str | None = None,
    download_dir: Path | None = None,
    db_path: Path | None = None,
) -> Path:
    download_dir = download_dir or DOWNLOADS_DIR
    db_path = db_path or DB_PATH
    ensure_directories()
    chosen_date = snapshot_date or _choose_snapshot_date(download_dir)
    files = _existing_snapshot_files_for(chosen_date, download_dir)
    if len(files) != len(PREFIXES):
        raise FileNotFoundError(
            f"Incomplete GIAS snapshot for {chosen_date}: {sorted(files)}"
        )

    db_path.parent.mkdir(exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        _init_schema(conn)

        for prefix in PREFIXES:
            csv_path = files[prefix]
            rows = _read_csv_rows(csv_path)
            if prefix == "edubasealldata":
                conn.executemany(
                    """
                    INSERT INTO schools (
                        urn, name, la_code, la_name, establishment_number,
                        status, status_code, type_name, type_code,
                        phase_name, phase_code, town, county, postcode,
                        latitude, longitude, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            row.get("URN", "").strip(),
                            row.get("EstablishmentName", "").strip(),
                            row.get("LA (code)", "").strip(),
                            row.get("LA (name)", "").strip(),
                            row.get("EstablishmentNumber", "").strip(),
                            row.get("EstablishmentStatus (name)", "").strip(),
                            row.get("EstablishmentStatus (code)", "").strip(),
                            row.get("TypeOfEstablishment (name)", "").strip(),
                            row.get("TypeOfEstablishment (code)", "").strip(),
                            row.get("PhaseOfEducation (name)", "").strip(),
                            row.get("PhaseOfEducation (code)", "").strip(),
                            row.get("Town", "").strip(),
                            row.get("County (name)", "").strip(),
                            row.get("Postcode", "").strip(),
                            *_bng_to_latlng(row.get("Easting"), row.get("Northing")),
                            json.dumps(row, ensure_ascii=False),
                        )
                        for row in rows
                        if row.get("URN", "").strip()
                        and row.get("EstablishmentName", "").strip()
                    ],
                )
            elif prefix == "allgroupsdata":
                conn.executemany(
                    """
                    INSERT INTO groups (group_uid, name, group_type, group_status, postcode, town, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            row.get("Group UID", "").strip(),
                            row.get("Group Name", "").strip(),
                            row.get("Group Type", "").strip(),
                            row.get("Group Status", "").strip(),
                            row.get("Group Postcode", "").strip(),
                            row.get("Group Town", "").strip(),
                            json.dumps(row, ensure_ascii=False),
                        )
                        for row in rows
                        if row.get("Group UID", "").strip()
                        and row.get("Group Name", "").strip()
                    ],
                )
            elif prefix == "alllinksdata":
                conn.executemany(
                    """
                    INSERT INTO memberships (urn, group_uid, group_name, joined_date, establishment_name, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            row.get("URN", "").strip(),
                            row.get("Group UID", "").strip(),
                            row.get("Group Name", "").strip(),
                            row.get("Joined date", "").strip(),
                            row.get("EstablishmentName", "").strip(),
                            json.dumps(row, ensure_ascii=False),
                        )
                        for row in rows
                        if row.get("URN", "").strip()
                        and row.get("Group UID", "").strip()
                    ],
                )
            elif prefix == "links_edubasealldata":
                conn.executemany(
                    """
                    INSERT INTO school_links (urn, link_urn, link_name, link_type, raw_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            row.get("URN", "").strip(),
                            row.get("LinkURN", "").strip(),
                            row.get("LinkName", "").strip(),
                            row.get("LinkType", "").strip(),
                            json.dumps(row, ensure_ascii=False),
                        )
                        for row in rows
                        if row.get("URN", "").strip() and row.get("LinkURN", "").strip()
                    ],
                )

    # Keep the snapshot date in metadata for the UI.
    return db_path


def ensure_database(
    download_dir: Path | None = None, db_path: Path | None = None
) -> Path:
    ensure_directories()
    lock_path = (download_dir or DOWNLOADS_DIR) / ".snapshot.lock"
    with FileLock(str(lock_path)):
        snapshot_date = _choose_snapshot_date(download_dir)
        database_path = db_path or DB_PATH
        import_snapshot(
            snapshot_date=snapshot_date,
            download_dir=download_dir,
            db_path=database_path,
        )
        return database_path


def fetch_all_schools(
    db_path: Path | None = None,
    status: str | None = None,
    phase: str | None = None,
    type_name: str | None = None,
    group_uid: str | None = None,
) -> list[dict[str, Any]]:
    db_path = db_path or DB_PATH
    query = (
        "SELECT urn, name, status, type_name, phase_name, latitude, longitude, town, la_name, postcode "
        "FROM schools WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    )
    params: list[str] = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if phase:
        query += " AND phase_name = ?"
        params.append(phase)
    if type_name:
        query += " AND type_name = ?"
        params.append(type_name)
    if group_uid:
        query += " AND urn IN (SELECT urn FROM memberships WHERE group_uid = ?)"
        params.append(group_uid)
    query += " ORDER BY name"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "urn": row[0],
            "name": row[1],
            "status": row[2],
            "type": row[3],
            "phase": row[4],
            "lat": row[5],
            "lng": row[6],
            "town": row[7],
            "la_name": row[8],
            "postcode": row[9],
        }
        for row in rows
    ]


def fetch_group_detail(
    group_uid: str, db_path: Path | None = None
) -> dict[str, Any] | None:
    db_path = db_path or DB_PATH
    with sqlite3.connect(db_path) as conn:
        group_row = conn.execute(
            "SELECT group_uid, name, group_type, group_status, postcode, town, raw_json FROM groups WHERE group_uid = ?",
            (group_uid,),
        ).fetchone()
        if group_row is None:
            return None

        members = conn.execute(
            """
            SELECT s.urn, s.name, s.type_name, s.status, s.phase_name, s.town, s.postcode
            FROM memberships m
            JOIN schools s ON s.urn = m.urn
            WHERE m.group_uid = ?
            ORDER BY s.name
            """,
            (group_uid,),
        ).fetchall()

    return {
        "uid": group_row[0],
        "name": group_row[1],
        "type": group_row[2],
        "status": group_row[3],
        "postcode": group_row[4],
        "town": group_row[5],
        "raw": json.loads(group_row[6]) if group_row[6] else {},
        "members": [
            {
                "urn": row[0],
                "name": row[1],
                "type": row[2],
                "status": row[3],
                "phase": row[4],
                "town": row[5],
                "postcode": row[6],
            }
            for row in members
        ],
    }


def search_records(
    query: str,
    mode: str = "school",
    db_path: Path | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    db_path = db_path or DB_PATH
    text = query.strip()
    if not text:
        return []
    term = f"%{text}%"
    with sqlite3.connect(db_path) as conn:
        if mode == "group":
            rows = conn.execute(
                """
                SELECT group_uid, name, group_type, group_status
                FROM groups
                WHERE lower(name) LIKE lower(?) OR lower(group_uid) LIKE lower(?)
                ORDER BY name
                LIMIT ?
                """,
                (term, term, limit),
            ).fetchall()
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "type": row[2],
                    "status": row[3],
                    "mode": "group",
                }
                for row in rows
            ]

        rows = conn.execute(
            """
            SELECT urn, name, type_name, phase_name, status, town
            FROM schools
            WHERE lower(name) LIKE lower(?) OR lower(urn) LIKE lower(?)
            ORDER BY name
            LIMIT ?
            """,
            (term, term, limit),
        ).fetchall()
        return [
            {
                "id": row[0],
                "name": row[1],
                "type": row[2],
                "phase": row[3],
                "status": row[4],
                "town": row[5],
                "mode": "school",
            }
            for row in rows
        ]


def fetch_filters(db_path: Path | None = None) -> dict[str, list[str]]:
    db_path = db_path or DB_PATH
    with sqlite3.connect(db_path) as conn:
        statuses = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT status FROM schools WHERE status IS NOT NULL AND status != '' ORDER BY status"
            ).fetchall()
        ]
        phases = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT phase_name FROM schools WHERE phase_name IS NOT NULL AND phase_name != '' ORDER BY phase_name"
            ).fetchall()
        ]
        types = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT type_name FROM schools WHERE type_name IS NOT NULL AND type_name != '' ORDER BY type_name"
            ).fetchall()
        ]
    return {"status": statuses, "phase": phases, "type": types}


def fetch_school_detail(urn: str, db_path: Path | None = None) -> dict[str, Any] | None:
    db_path = db_path or DB_PATH
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT urn, name, la_code, la_name, establishment_number, status, status_code, type_name, type_code, phase_name, phase_code, town, county, postcode, latitude, longitude, raw_json FROM schools WHERE urn = ?",
            (urn,),
        ).fetchone()
    if row is None:
        return None
    payload = {
        "urn": row[0],
        "name": row[1],
        "la_code": row[2],
        "la_name": row[3],
        "establishment_number": row[4],
        "status": row[5],
        "status_code": row[6],
        "type_name": row[7],
        "type_code": row[8],
        "phase_name": row[9],
        "phase_code": row[10],
        "town": row[11],
        "county": row[12],
        "postcode": row[13],
        "latitude": row[14],
        "longitude": row[15],
        "raw": json.loads(row[16]) if row[16] else {},
    }
    return payload
