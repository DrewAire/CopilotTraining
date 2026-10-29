import csv
from pathlib import Path

from fastapi.testclient import TestClient

from gias_visualiser.app import create_app
from gias_visualiser.data import import_snapshot, fetch_all_schools


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def test_import_snapshot_creates_school_and_group_rows(tmp_path):
    downloads = tmp_path / "downloads"
    data_dir = tmp_path / "data"
    downloads.mkdir()
    data_dir.mkdir()

    school_headers = [
        "URN",
        "EstablishmentName",
        "LA (code)",
        "LA (name)",
        "EstablishmentNumber",
        "EstablishmentStatus (name)",
        "EstablishmentStatus (code)",
        "TypeOfEstablishment (name)",
        "TypeOfEstablishment (code)",
        "PhaseOfEducation (name)",
        "PhaseOfEducation (code)",
        "Town",
        "County (name)",
        "Postcode",
        "Easting",
        "Northing",
    ]
    school_rows = [
        [
            "100000",
            "Aldgate School",
            "201",
            "City of London",
            "1",
            "Open",
            "1",
            "Voluntary aided",
            "4",
            "Primary",
            "2",
            "London",
            "City of London",
            "EC3A 5DE",
            "533498",
            "181201",
        ]
    ]
    with (downloads / "edubasealldata20261001.csv").open(
        "w", encoding="cp1252", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(school_headers)
        writer.writerow(
            [
                "100000",
                "Aldgate School",
                "201",
                "City of London",
                "1",
                "Open",
                "1",
                "Voluntary aided",
                "4",
                "Primary",
                "2",
                "London",
                "City of London",
                "EC3A 5DE",
                "533498",
                "181201",
            ]
        )
        writer.writerow(
            [
                "100001",
                "St Paul’s Academy",
                "201",
                "City of London",
                "2",
                "Open",
                "1",
                "Voluntary aided",
                "4",
                "Secondary",
                "3",
                "London",
                "City of London",
                "EC3A 5DF",
                "533500",
                "181250",
            ]
        )

    group_headers = [
        "Group UID",
        "Group Name",
        "Group Type",
        "Group Status",
        "Group Postcode",
        "Group Town",
    ]
    group_rows = [["80637", "Trust A", "Academy Trust", "Open", "EC1A 1AA", "London"]]
    _write_csv(downloads / "allgroupsdata20261001.csv", group_headers, group_rows)

    membership_headers = [
        "URN",
        "Group UID",
        "Group Name",
        "Joined date",
        "EstablishmentName",
    ]
    membership_rows = [["100000", "80637", "Trust A", "01/01/2024", "Aldgate School"]]
    _write_csv(
        downloads / "alllinksdata20261001.csv", membership_headers, membership_rows
    )

    links_headers = ["URN", "LinkURN", "LinkName", "LinkType"]
    links_rows = [["100000", "100001", "Former name", "Predecessor - merged"]]
    _write_csv(
        downloads / "links_edubasealldata20261001.csv", links_headers, links_rows
    )

    database_path = data_dir / "gias.sqlite3"
    import_snapshot(
        snapshot_date="20261001", download_dir=downloads, db_path=database_path
    )

    schools = fetch_all_schools(db_path=database_path)
    assert len(schools) == 2
    assert schools[0]["name"] == "Aldgate School"
    assert schools[1]["name"] == "St Paul’s Academy"
    assert abs(schools[0]["lat"] - 51.513) < 1.0
    assert abs(schools[0]["lng"] + 0.08) < 1.0


def test_search_route_returns_matching_school_and_group(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    data_dir = tmp_path / "data"
    downloads.mkdir()
    data_dir.mkdir()

    school_headers = [
        "URN",
        "EstablishmentName",
        "LA (code)",
        "LA (name)",
        "EstablishmentNumber",
        "EstablishmentStatus (name)",
        "EstablishmentStatus (code)",
        "TypeOfEstablishment (name)",
        "TypeOfEstablishment (code)",
        "PhaseOfEducation (name)",
        "PhaseOfEducation (code)",
        "Town",
        "County (name)",
        "Postcode",
        "Easting",
        "Northing",
    ]
    with (downloads / "edubasealldata20261001.csv").open(
        "w", encoding="cp1252", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(school_headers)
        writer.writerow(
            [
                "100000",
                "Aldgate School",
                "201",
                "City of London",
                "1",
                "Open",
                "1",
                "Voluntary aided",
                "4",
                "Primary",
                "2",
                "London",
                "City of London",
                "EC3A 5DE",
                "533498",
                "181201",
            ]
        )

    group_headers = [
        "Group UID",
        "Group Name",
        "Group Type",
        "Group Status",
        "Group Postcode",
        "Group Town",
    ]
    _write_csv(
        downloads / "allgroupsdata20261001.csv",
        group_headers,
        [["80637", "Trust A", "Academy Trust", "Open", "EC1A 1AA", "London"]],
    )
    _write_csv(
        downloads / "alllinksdata20261001.csv",
        ["URN", "Group UID", "Group Name", "Joined date", "EstablishmentName"],
        [["100000", "80637", "Trust A", "01/01/2024", "Aldgate School"]],
    )
    _write_csv(
        downloads / "links_edubasealldata20261001.csv",
        ["URN", "LinkURN", "LinkName", "LinkType"],
        [["100000", "100001", "Former name", "Predecessor - merged"]],
    )

    database_path = data_dir / "gias.sqlite3"
    import_snapshot(
        snapshot_date="20261001", download_dir=downloads, db_path=database_path
    )

    monkeypatch.setattr("gias_visualiser.app.DB_PATH", database_path)
    app = create_app()
    client = TestClient(app)

    school_response = client.get(
        "/api/search", params={"mode": "school", "q": "Aldgate"}
    )
    assert school_response.status_code == 200
    assert any(item["name"] == "Aldgate School" for item in school_response.json())

    group_response = client.get("/api/search", params={"mode": "group", "q": "Trust"})
    assert group_response.status_code == 200
    assert any(item["name"] == "Trust A" for item in group_response.json())
