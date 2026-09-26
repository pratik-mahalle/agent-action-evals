"""An existing database-writing function with its first response hidden.

Run: python examples/wrapped_sqlite_tool.py
No model or remote service is used. The independent read proves the local write.
"""

import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from agent_action_evals import Fault, ToolBoundary


def main():
    with tempfile.TemporaryDirectory(prefix="aae-boundary-") as directory:
        database = Path(directory) / "notes.sqlite"
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")

        # Ordinary application code. No World, operation contract, or test API.
        def create_note(body: str) -> dict:
            with closing(sqlite3.connect(database)) as db, db:
                cursor = db.execute("INSERT INTO notes (body) VALUES (?)", (body,))
                note_id = cursor.lastrowid
            return {"id": note_id, "body": body}

        boundary = ToolBoundary((Fault("create_note", "response_lost_after_commit"),))
        tested_create_note = boundary.wrap(create_note)

        try:
            tested_create_note("Check the deployed service")
        except TimeoutError as exc:
            print(f"Caller observed: {exc}")
            # The agent/application decides what to do next. The boundary stops here.

        # Evaluator-only query, through a fresh database connection.
        with closing(sqlite3.connect(database)) as db:
            rows = db.execute("SELECT id, body FROM notes").fetchall()
        assert len(rows) == 1, rows
        print(f"Independent database check: {len(rows)} committed note; no automatic retry")
        print(json.dumps(boundary.report(), indent=2))


if __name__ == "__main__":
    main()
