"""Paths, ports and the source registry. Everything can be overridden by env vars."""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CERT_DB", os.path.join(BASE, "certcheck.db"))
EVIDENCE_DIR = os.environ.get("CERT_EVIDENCE", os.path.join(BASE, "evidence"))
MOCK_PORT = 8000
APP_PORT = 8080

with open(os.path.join(BASE, "sources.json"), encoding="utf-8") as f:
    SOURCES = json.load(f)
