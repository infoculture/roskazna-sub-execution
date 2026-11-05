"""Командная строка конвейера.

    python -m roskazna_kbs catalog     # обойти каталог → data/catalog/catalog.csv
    python -m roskazna_kbs download    # скачать ZIP → data/raw/zip, data/catalog/files.csv
    python -m roskazna_kbs inventory   # реестр вложенных файлов → data/catalog/members.csv
    python -m roskazna_kbs profile     # профиль книг/листов → data/catalog/sheets.csv
    python -m roskazna_kbs build       # разобрать книги и собрать БД → data/db/roskazna_kbs.duckdb
    python -m roskazna_kbs export      # выгрузить таблицы БД в Parquet → data/export/
    python -m roskazna_kbs all         # всё по порядку

    python -m roskazna_kbs extract FILE_ID [DIR]   # распаковать один пакет для ручного изучения
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

STEPS = ["catalog", "download", "inventory", "profile", "build", "export"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="roskazna_kbs", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=STEPS + ["all", "extract"])
    parser.add_argument("args", nargs="*", help="для extract: FILE_ID [DIR]")
    parser.add_argument("--force", action="store_true", help="download: перекачать скачанные файлы; build: разобрать все пакеты заново")
    parser.add_argument("--limit", type=int, help="обработать не больше N пакетов (для отладки)")
    parser.add_argument("--years", help="ограничить build годами, например 2024-2026 или 2005,2010")
    parser.add_argument("--workers", type=int, help="build: число процессов разбора (по умолчанию min(8, CPU-2))")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "extract":
        from .archive import extract_to_dir, read_files
        from .config import EXTRACTED_DIR

        if not args.args:
            parser.error("extract: укажите FILE_ID")
        file_id = args.args[0]
        row = next((r for r in read_files() if r["file_id"] == file_id), None)
        if row is None:
            parser.error(f"нет файла {file_id} в files.csv")
        out = Path(args.args[1]) if len(args.args) > 1 else EXTRACTED_DIR / file_id
        n = extract_to_dir(row, out)
        logging.info("extract: %s files → %s", n, out)
        return 0

    steps = STEPS if args.command == "all" else [args.command]
    for step in steps:
        if step == "catalog":
            from . import catalog

            cards = catalog.crawl()
            catalog.write_catalog(cards)
            logging.info("catalog: %s cards", len(cards))
        elif step == "download":
            from . import download

            rows = download.download_all(force=args.force, limit=args.limit)
            logging.info("download: %s files", len(rows))
        elif step == "inventory":
            from . import archive

            rows = archive.inventory_all()
            logging.info("inventory: %s members", len(rows))
        elif step == "profile":
            from . import profile

            rows = profile.profile_all(limit=args.limit)
            logging.info("profile: %s sheets", len(rows))
        elif step == "build":
            from . import build

            build.build_database(years=_parse_years(args.years), limit=args.limit, force=args.force,
                                    workers=args.workers)
        elif step == "export":
            from . import build

            build.export_parquet()
    return 0


def _parse_years(spec: str | None) -> set[int] | None:
    if not spec:
        return None
    years: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            years.update(range(int(a), int(b) + 1))
        elif part.strip():
            years.add(int(part))
    return years


if __name__ == "__main__":
    sys.exit(main())
