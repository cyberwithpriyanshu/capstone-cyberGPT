# app/knowledge/store.py
#
# PURPOSE: Saving chunks to disk and loading them back.
#
# FORMAT: "JSON Lines" (.jsonl): a plain text file with ONE chunk per line, each line being
# a small JSON object. WHY: you can open it in any editor, Git shows readable changes, it
# needs no database, and we can read it one line at a time.
#
# One file per source (kev.jsonl, attack.jsonl, owasp.jsonl), so refreshing one source never
# touches the others.

from __future__ import annotations

import json
from pathlib import Path

from app.knowledge.models import Chunk

# The project's top folder: store.py -> knowledge -> app -> (project root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Where the ingested knowledge lives by default: <project>/data/knowledge/
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "knowledge"


def save_chunks(path: Path, chunks: list[Chunk]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" keeps the file identical on Windows and Linux (no surprise CRLF changes).
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for chunk in chunks:
            # ensure_ascii=False keeps characters like "é" readable instead of "\u00e9".
            f.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")


def load_chunks(path: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                chunks.append(Chunk.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError, ValueError) as err:
                # Say exactly where the damage is, instead of a confusing crash later.
                raise ValueError(f"{path.name}, line {line_number}: bad chunk ({err})") from err
    return chunks


def load_directory(directory: Path) -> list[Chunk]:
    """Loads every .jsonl file in a folder. A missing folder just means 'nothing ingested yet'."""
    if not directory.is_dir():
        return []
    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.jsonl")):
        chunks.extend(load_chunks(path))
    return chunks