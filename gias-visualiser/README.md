# GIAS Visualiser

A local browser-based visualiser for public GIAS CSV downloads. The application checks for the latest complete data snapshot, imports the CSV files into SQLite, and opens a local map in the browser.

## Prerequisites

- Python 3.12+
- `uv` installed

## Run the app

From the project root:

```bash
uv run gias-visualiser
```

This will:

1. Check the `downloads/` folder for today's GIAS CSVs.
2. Download any missing files from the public GIAS URL.
3. Import the data into `data/gias.sqlite3`.
4. Open the browser at `http://127.0.0.1:8000`.

## Data source

The app uses the public GIAS CSVs from the official download endpoint and stores the outputs locally.

## Development

```bash
uv run pytest
```

## Notes

- The application is designed as a local demonstration, not a production-hosted service.
- The map displays the schools that are available in the local snapshot and include coordinates.
- If a full same-day set is not available, the app falls back to the latest valid local snapshot instead of starting with incomplete data.
