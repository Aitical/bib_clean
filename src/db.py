import sqlite3
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

DB_NAME = ".bib_manager.db"

def get_connection(db_path: str = DB_NAME) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn: sqlite3.Connection):
    cursor = conn.cursor()

    # Sources Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filepath TEXT UNIQUE NOT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Entries Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL,
        original_key TEXT NOT NULL,

        -- Deduplication Fingerprints
        normalized_title TEXT NOT NULL,
        authors_fingerprint TEXT,
        year TEXT,

        -- Content
        raw_bib_json TEXT NOT NULL,
        original_bib_text TEXT,

        -- Processing State
        cluster_id INTEGER,
        status TEXT DEFAULT 'pending',
        -- status enum: pending, primary, duplicate, excluded

        -- Final Output
        final_key TEXT,

        FOREIGN KEY(source_id) REFERENCES sources(id),
        UNIQUE(source_id, original_key)
    );
    """)

    # Indexes for performance
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entries_norm_title ON entries(normalized_title);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entries_authors ON entries(authors_fingerprint);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entries_cluster ON entries(cluster_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entries_orig_key ON entries(original_key);")

    conn.commit()

def add_source(conn: sqlite3.Connection, filepath: str) -> int:
    """Adds a source file if not exists, returns source_id."""
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO sources (filepath) VALUES (?)", (filepath,))
        source_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        cursor.execute("SELECT id FROM sources WHERE filepath = ?", (filepath,))
        result = cursor.fetchone()
        source_id = result['id'] if result else None
    conn.commit()
    return source_id

def add_entry(
    conn: sqlite3.Connection,
    source_id: int,
    original_key: str,
    normalized_title: str,
    authors_fingerprint: str,
    year: str,
    raw_bib_dict: Dict[str, Any],
    original_bib_text: str = ""
) -> int:
    """Adds a bib entry. Updates if exists (for same source and key)."""
    cursor = conn.cursor()
    raw_json = json.dumps(raw_bib_dict)

    query = """
    INSERT INTO entries (
        source_id, original_key, normalized_title, authors_fingerprint, year,
        raw_bib_json, original_bib_text, status
    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
    ON CONFLICT(source_id, original_key) DO UPDATE SET
        normalized_title=excluded.normalized_title,
        authors_fingerprint=excluded.authors_fingerprint,
        year=excluded.year,
        raw_bib_json=excluded.raw_bib_json,
        original_bib_text=excluded.original_bib_text;
    """

    cursor.execute(query, (
        source_id, original_key, normalized_title, authors_fingerprint, year,
        raw_json, original_bib_text
    ))
    conn.commit()
    return cursor.lastrowid
