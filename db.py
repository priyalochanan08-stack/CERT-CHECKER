"""SQLite audit log. Nothing is ever deleted: failed and pending items stay visible."""
import datetime
import sqlite3
import threading

import config

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS records(
  record_id TEXT PRIMARY KEY, credential_id TEXT, issuer TEXT, candidate_name TEXT,
  claimed_course TEXT, claimed_date TEXT, consent INTEGER, consent_ts TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS plans(
  plan_id TEXT PRIMARY KEY, record_id TEXT, payload_json TEXT, approved_hash TEXT,
  status TEXT, approved_at TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS executions(
  exec_id TEXT PRIMARY KEY, plan_id TEXT, idem_key TEXT UNIQUE, status TEXT, page_state TEXT,
  screenshot_path TEXT, text_path TEXT, final_url TEXT, http_status INTEGER, source_id TEXT,
  retrieved_at TEXT, attempts INTEGER, backend TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS decisions(
  decision_id TEXT PRIMARY KEY, record_id TEXT, exec_id TEXT, result TEXT, reason TEXT,
  inputs_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS verifications(
  ver_id TEXT PRIMARY KEY, exec_id TEXT, decision_id TEXT, passed INTEGER, notes_json TEXT,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS steps(
  step_id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT, step TEXT, detail TEXT, ts TEXT);
"""


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def init(path=None):
    global _conn
    with _lock:
        _conn = sqlite3.connect(path or config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()


def q(sql, args=(), one=False):
    with _lock:
        cur = _conn.execute(sql, args)
        if sql.lstrip().upper().startswith("SELECT"):
            rows = cur.fetchall()
            return (rows[0] if rows else None) if one else rows
        _conn.commit()


def insert(table, prefix, idcol, **fields):
    """Insert a row with a stable ID like REC-001. Raises sqlite3.IntegrityError on UNIQUE clash."""
    with _lock:
        n = _conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] + 1
        fields[idcol] = f"{prefix}-{n:03d}"
        cols = ",".join(fields)
        marks = ",".join("?" * len(fields))
        _conn.execute(f"INSERT INTO {table}({cols}) VALUES({marks})", tuple(fields.values()))
        _conn.commit()
        return fields[idcol]


def update(table, idcol, idval, **fields):
    sets = ",".join(f"{k}=?" for k in fields)
    q(f"UPDATE {table} SET {sets} WHERE {idcol}=?", tuple(fields.values()) + (idval,))


def step(record_id, name, detail=""):
    q("INSERT INTO steps(record_id,step,detail,ts) VALUES(?,?,?,?)", (record_id, name, detail, now()))
