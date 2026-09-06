"""
db.py
-----
Minimal SQLite data-access layer. Kept dependency-free (stdlib sqlite3) so
the prototype runs anywhere without a DB server. A production deployment
should swap this for PostgreSQL + an ORM (SQLAlchemy) — the function
signatures here are written so that swap only touches this file.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = Path(__file__).parent / "instance" / "compliance.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('officer','admin')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT,
    brand TEXT,
    category TEXT,
    image_path TEXT NOT NULL,
    pdp_width_cm REAL,
    pdp_height_cm REAL,
    ocr_text TEXT,
    overall_status TEXT,
    score REAL,
    declarations_json TEXT,
    scanned_by TEXT,
    location TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # seed a default admin + officer account if empty
        row = conn.execute("SELECT COUNT(*) c FROM users").fetchone()
        if row["c"] == 0:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                ("admin", generate_password_hash("admin123"), "admin", datetime.utcnow().isoformat()),
            )
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                ("officer", generate_password_hash("officer123"), "officer", datetime.utcnow().isoformat()),
            )


def verify_user(username, password):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            return {"id": row["id"], "username": row["username"], "role": row["role"]}
    return None


def create_user(username, password, role):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (username, generate_password_hash(password), role, datetime.utcnow().isoformat()),
        )


def save_scan(*, product_name, brand, category, image_path, pdp_width_cm, pdp_height_cm,
              ocr_text, overall_status, score, declarations, scanned_by, location, notes):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO scans
               (product_name, brand, category, image_path, pdp_width_cm, pdp_height_cm,
                ocr_text, overall_status, score, declarations_json, scanned_by, location, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (product_name, brand, category, image_path, pdp_width_cm, pdp_height_cm,
             ocr_text, overall_status, score, json.dumps(declarations), scanned_by, location, notes,
             datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def get_scan(scan_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        return dict(row) if row else None


def list_scans(search=None, status=None, limit=200):
    query = "SELECT * FROM scans WHERE 1=1"
    params = []
    if search:
        query += " AND (product_name LIKE ? OR brand LIKE ? OR category LIKE ?)"
        like = f"%{search}%"
        params += [like, like, like]
    if status:
        query += " AND overall_status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def dashboard_stats():
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM scans").fetchone()["c"]
        by_status = conn.execute(
            "SELECT overall_status, COUNT(*) c FROM scans GROUP BY overall_status"
        ).fetchall()
        by_category = conn.execute(
            "SELECT COALESCE(category,'Uncategorised') category, COUNT(*) c FROM scans GROUP BY category ORDER BY c DESC LIMIT 8"
        ).fetchall()
        recent = conn.execute(
            "SELECT id, product_name, overall_status, score, created_at FROM scans ORDER BY created_at DESC LIMIT 8"
        ).fetchall()
        avg_score = conn.execute("SELECT AVG(score) a FROM scans").fetchone()["a"] or 0
        return {
            "total": total,
            "by_status": {r["overall_status"]: r["c"] for r in by_status},
            "by_category": [dict(r) for r in by_category],
            "recent": [dict(r) for r in recent],
            "avg_score": round(avg_score, 1),
        }
