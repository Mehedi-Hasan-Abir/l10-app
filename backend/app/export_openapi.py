"""Write the OpenAPI document: `python -m app.export_openapi [path]` (default `../frontend/openapi.json`).

The frontend generates its TypeScript types from this file (`npm run gen:api`). Building the document needs no
database: the app is created, but its lifespan (engine, clock, hub) is never started.
"""

import json
import sys
from pathlib import Path

from app.main import create_app

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    document = create_app().openapi()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {target} ({len(document['paths'])} paths, {len(document['components']['schemas'])} schemas)"
    )


if __name__ == "__main__":
    main()
