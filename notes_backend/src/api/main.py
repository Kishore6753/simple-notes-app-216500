from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

OPENAPI_TAGS = [
    {"name": "Health", "description": "Service health and diagnostics."},
    {"name": "Notes", "description": "CRUD operations for notes."},
]


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _get_sqlite_path() -> str:
    """
    Resolve the SQLite database file path.

    Uses SQLITE_DB env var when provided; otherwise uses the database container's default
    absolute path used in this workspace.
    """
    # NOTE: The database container declares SQLITE_DB as the env var name.
    env_path = os.getenv("SQLITE_DB")
    if env_path:
        return env_path

    # Fallback path for local dev in this multi-container workspace.
    return "/home/kavia/workspace/code-generation/simple-notes-app-216501/database/myapp.db"


@contextmanager
def _db_conn():
    """Context manager yielding a sqlite3 connection with row factory configured."""
    db_path = _get_sqlite_path()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _init_db() -> None:
    """Create notes table if it does not exist."""
    with _db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


class Note(BaseModel):
    """A persisted note."""

    id: int = Field(..., description="Unique numeric ID of the note.")
    title: str = Field(..., min_length=1, max_length=200, description="Note title.")
    content: str = Field(..., description="Note body content.")
    created_at: str = Field(..., description="ISO-8601 UTC timestamp when the note was created.")
    updated_at: str = Field(..., description="ISO-8601 UTC timestamp when the note was last updated.")


class NoteCreate(BaseModel):
    """Payload for creating a note."""

    title: str = Field(..., min_length=1, max_length=200, description="Note title.")
    content: str = Field("", description="Note body content.")


class NoteUpdate(BaseModel):
    """Payload for updating a note."""

    title: Optional[str] = Field(None, min_length=1, max_length=200, description="New title.")
    content: Optional[str] = Field(None, description="New content.")


app = FastAPI(
    title="Simple Notes API",
    description=(
        "A simple notes API providing CRUD endpoints backed by SQLite.\n\n"
        "Frontend (React) runs on http://localhost:3000 and should call this API on http://localhost:3001."
    ),
    version="1.0.0",
    openapi_tags=OPENAPI_TAGS,
)

# Restrict CORS to the frontend origin by default (but allow overriding via env).
allowed_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[allowed_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    """Initialize the SQLite database schema at service startup."""
    _init_db()


@app.get("/", tags=["Health"], summary="Health check")
def health_check():
    """
    Health check endpoint.

    Returns:
        JSON payload confirming that the service is up.
    """
    return {"message": "Healthy"}


# PUBLIC_INTERFACE
@app.get(
    "/notes",
    response_model=List[Note],
    tags=["Notes"],
    summary="List notes",
    description="Returns all notes ordered by updated_at DESC, then id DESC.",
    operation_id="list_notes",
)
def list_notes() -> List[Note]:
    """List all notes."""
    with _db_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, content, created_at, updated_at FROM notes ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        return [Note(**dict(r)) for r in rows]


# PUBLIC_INTERFACE
@app.post(
    "/notes",
    response_model=Note,
    tags=["Notes"],
    summary="Create a note",
    description="Create a note with title/content. Timestamps are generated server-side in UTC.",
    operation_id="create_note",
)
def create_note(payload: NoteCreate) -> Note:
    """Create and persist a new note."""
    now = _utc_now_iso()
    with _db_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notes (title, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (payload.title.strip(), payload.content, now, now),
        )
        conn.commit()
        note_id = int(cur.lastrowid)

        row = conn.execute(
            "SELECT id, title, content, created_at, updated_at FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        return Note(**dict(row))


# PUBLIC_INTERFACE
@app.get(
    "/notes/{note_id}",
    response_model=Note,
    tags=["Notes"],
    summary="Get a note",
    description="Fetch a single note by id.",
    operation_id="get_note",
)
def get_note(note_id: int) -> Note:
    """Get a single note by ID."""
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT id, title, content, created_at, updated_at FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Note not found")
        return Note(**dict(row))


# PUBLIC_INTERFACE
@app.put(
    "/notes/{note_id}",
    response_model=Note,
    tags=["Notes"],
    summary="Update a note",
    description="Update title/content for a note. updated_at is set server-side.",
    operation_id="update_note",
)
def update_note(note_id: int, payload: NoteUpdate) -> Note:
    """Update an existing note by ID."""
    with _db_conn() as conn:
        existing = conn.execute(
            "SELECT id, title, content, created_at, updated_at FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Note not found")

        new_title = payload.title.strip() if payload.title is not None else existing["title"]
        new_content = payload.content if payload.content is not None else existing["content"]
        now = _utc_now_iso()

        conn.execute(
            "UPDATE notes SET title = ?, content = ?, updated_at = ? WHERE id = ?",
            (new_title, new_content, now, note_id),
        )
        conn.commit()

        row = conn.execute(
            "SELECT id, title, content, created_at, updated_at FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        return Note(**dict(row))


# PUBLIC_INTERFACE
@app.delete(
    "/notes/{note_id}",
    tags=["Notes"],
    summary="Delete a note",
    description="Delete a note by id.",
    operation_id="delete_note",
)
def delete_note(note_id: int):
    """Delete a note by ID."""
    with _db_conn() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Note not found")
    return {"deleted": True, "id": note_id}
