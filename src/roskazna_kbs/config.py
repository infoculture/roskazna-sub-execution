"""Пути и константы проекта."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CERTS_DIR = ROOT / "certs"
DATA_DIR = Path(os.environ.get("ROSKAZNA_KBS_DATA", ROOT / "data"))
CATALOG_DIR = DATA_DIR / "catalog"
RAW_DIR = DATA_DIR / "raw"
RAW_ZIP_DIR = RAW_DIR / "zip"
EXTRACTED_DIR = RAW_DIR / "extracted"
DB_DIR = DATA_DIR / "db"
EXPORT_DIR = DATA_DIR / "export"
CACHE_DIR = DATA_DIR / ".cache"

CATALOG_CSV = CATALOG_DIR / "catalog.csv"
FILES_CSV = CATALOG_DIR / "files.csv"
MEMBERS_CSV = CATALOG_DIR / "members.csv"
SHEETS_CSV = CATALOG_DIR / "sheets.csv"
DB_PATH = DB_DIR / "roskazna_kbs.duckdb"

BASE_URL = "https://roskazna.gov.ru"
CATALOG_URL = f"{BASE_URL}/ispolnenie-byudzhetov/konsolidirovannye-byudzhety-subektov-rossijskoj-federacii/"

# Внутренние значения фильтра «Тип» веб-интерфейса (не документированный API).
PUBLICATION_TYPES = {
    48: ("monthly", "ежемесячный"),
    49: ("annual", "годовой"),
}

USER_AGENT = "roskazna-kbs-execution/0.1 (open budget data research; +https://infoculture.github.io/finguide/)"

# Минимальный интервал между запросами к порталу, секунды.
REQUEST_INTERVAL = 2.0
