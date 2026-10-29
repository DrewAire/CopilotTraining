from __future__ import annotations

import webbrowser

import uvicorn

from .app import create_app
from .data import ensure_database


def main() -> None:
    ensure_database()
    app = create_app()
    url = "http://127.0.0.1:8000"
    webbrowser.open_new_tab(url)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
