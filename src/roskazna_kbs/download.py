"""Загрузка ZIP-пакетов из каталога и реестр файлов.

* Уникальный ключ файла — путь на портале (``file_path``). Одна ссылка может
  встречаться в нескольких карточках каталога.
* Локальное имя: ``data/raw/zip/<publication_type>/<filter_year>/<basename>``.
  При совпадении имён к имени добавляется короткий хеш пути.
* Большие пакеты (до ~90 МБ) докачиваются по ``Range`` в ``*.part``, если
  соединение оборвалось. Целостность проверяется по ``Content-Length`` и
  ``zipfile.testzip()``.
* Повторный запуск скачивает только отсутствующие файлы.
* ``data/catalog/files.csv`` — реестр: URL, локальный путь, размер, SHA-256,
  HTTP-статус, дата загрузки, результат проверки архива.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import time
import zipfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import requests

from .catalog import read_catalog
from .config import DATA_DIR, FILES_CSV, RAW_ZIP_DIR
from .http import _throttle, make_session

log = logging.getLogger(__name__)

FILE_FIELDS = [
    "package_no",
    "file_id",
    "publication_type",
    "filter_year",
    "card_title",
    "file_path",
    "url",
    "local_path",
    "size_bytes",
    "sha256",
    "http_status",
    "content_type",
    "last_modified",
    "zip_ok",
    "downloaded_at",
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_id_for(file_path: str) -> str:
    """Стабильный идентификатор файла — первые 12 символов SHA-1 от пути на портале."""
    return hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:12]


def unique_files(catalog: list[dict]) -> "OrderedDict[str, dict]":
    """Схлопывает карточки до уникальных файлов. Атрибуты берутся из первой карточки.

    Порядок загрузки: сначала годовые пакеты (по одному на год — быстрый обзор
    всех макетов), затем ежемесячные; внутри типа — от новых лет к старым.
    """
    files: OrderedDict[str, dict] = OrderedDict()
    order = {"annual": 0, "monthly": 1}
    for row in sorted(catalog, key=lambda r: (order.get(r["publication_type"], 9), -int(r["filter_year"]))):
        files.setdefault(row["file_path"], row)
    return files


def local_name(row: dict, taken: set[str]) -> Path:
    base = unquote(PurePosixPath(row["file_path"]).name)
    rel = Path(row["publication_type"]) / str(row["filter_year"]) / base
    if str(rel) in taken:
        stem, suffix = Path(base).stem, Path(base).suffix
        rel = rel.with_name(f"{stem}__{file_id_for(row['file_path'])[:6]}{suffix}")
    taken.add(str(rel))
    return rel


def read_files_registry(path: Path = FILES_CSV) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["file_path"]: r for r in csv.DictReader(fh)}


def write_files_registry(rows: list[dict], path: Path = FILES_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FILE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def zip_ok(path: Path) -> str:
    """'ok', 'rar-ok' (RAR под расширением .zip), 'not-zip' или описание ошибки."""
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head == b"Rar!":
        import rarfile

        try:
            with rarfile.RarFile(str(path)) as r:
                r.testrar()
            return "rar-ok"
        except Exception as exc:  # noqa: BLE001
            return f"bad-rar:{type(exc).__name__}"
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
        return "ok" if bad is None else f"crc:{bad}"
    except zipfile.BadZipFile:
        return "not-zip" if head != b"PK\x03\x04" else "bad-zip"


def fetch_resumable(session: requests.Session, url: str, target: Path, attempts: int = 8) -> requests.Response:
    """Скачивает файл в ``target`` с докачкой по Range. Возвращает последний ответ."""
    tmp = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    resp: requests.Response | None = None
    for attempt in range(1, attempts + 1):
        have = tmp.stat().st_size if tmp.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        _throttle()
        try:
            resp = session.get(url, headers=headers, stream=True, timeout=(30, 300))
            if resp.status_code == 416:  # уже всё скачано
                tmp.replace(target)
                return resp
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            if resp.status_code not in (200, 206):
                return resp
            mode = "ab" if resp.status_code == 206 else "wb"
            total = None
            if resp.status_code == 206 and "content-range" in resp.headers:
                total = int(resp.headers["content-range"].rsplit("/", 1)[-1])
            elif "content-length" in resp.headers:
                total = int(resp.headers["content-length"])
            with open(tmp, mode) as fh:
                for chunk in resp.iter_content(1 << 16):
                    fh.write(chunk)
            size = tmp.stat().st_size
            if total is not None and size < total:
                raise requests.ConnectionError(f"short read {size}/{total}")
            tmp.replace(target)
            return resp
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError, requests.exceptions.ChunkedEncodingError) as exc:
            pause = min(300, 10 * 2 ** (attempt - 1))
            log.warning("attempt %s/%s for %s failed (%s); sleep %ss", attempt, attempts, url, exc, pause)
            time.sleep(pause)
    raise RuntimeError(f"Не удалось скачать {url}")


def download_all(force: bool = False, limit: int | None = None) -> list[dict]:
    catalog = read_catalog()
    registry = read_files_registry()
    session = make_session()
    taken: set[str] = {str((DATA_DIR / r["local_path"]).relative_to(RAW_ZIP_DIR))
                       for r in registry.values() if r.get("local_path")}
    out: list[dict] = []
    files = unique_files(catalog)
    # package_no — постоянный номер пакета: присваивается один раз при первом
    # появлении в каталоге и не меняется (основа суррогатных ключей report_id).
    numbers = {fp: int(r["package_no"]) for fp, r in registry.items() if r.get("package_no")}
    next_no = max(numbers.values(), default=0) + 1
    for fp in sorted(files, key=lambda fp: (files[fp]["filter_year"], files[fp]["publication_type"], fp)):
        if fp not in numbers:
            numbers[fp] = next_no
            next_no += 1
    for i, (file_path, row) in enumerate(files.items(), start=1):
        if limit is not None and i > limit:
            break
        prev = registry.get(file_path)
        if prev and prev.get("local_path"):
            target = DATA_DIR / prev["local_path"]
        else:
            target = RAW_ZIP_DIR / local_name(row, taken)
        record = {
            "package_no": numbers[file_path],
            "file_id": file_id_for(file_path),
            "publication_type": row["publication_type"],
            "filter_year": row["filter_year"],
            "card_title": row["card_title"],
            "file_path": file_path,
            "url": row["url"],
            "local_path": str(target.relative_to(DATA_DIR)),  # относительно data/
        }
        if target.exists() and not force and prev and prev.get("sha256"):
            record.update({k: prev.get(k, "") for k in FILE_FIELDS if k not in record})
            out.append(record)
            continue
        if target.exists() and not force and zip_ok(target) in ("ok", "rar-ok"):
            # Файл уже скачан, но реестр не успел сохраниться (прерванный запуск).
            record.update({
                "size_bytes": target.stat().st_size, "sha256": sha256_of(target), "zip_ok": "ok",
                "http_status": "", "content_type": "", "last_modified": "",
                "downloaded_at": datetime.fromtimestamp(target.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            })
            out.append(record)
            continue
        log.info("[%s/%s] %s", i, len(files), file_path)
        resp = fetch_resumable(session, row["url"], target)
        record["http_status"] = resp.status_code
        record["content_type"] = resp.headers.get("content-type", "")
        record["last_modified"] = resp.headers.get("last-modified", "")
        record["downloaded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if target.exists() and resp.status_code in (200, 206, 416):
            record["size_bytes"] = target.stat().st_size
            record["sha256"] = sha256_of(target)
            record["zip_ok"] = zip_ok(target)
            if record["zip_ok"] not in ("ok", "rar-ok"):
                log.warning("archive check %s: %s", target, record["zip_ok"])
        else:
            log.warning("HTTP %s for %s", resp.status_code, row["url"])
            record.update({"size_bytes": "", "sha256": "", "zip_ok": ""})
        out.append(record)
        if i % 10 == 0:  # промежуточное сохранение реестра
            done = {o["file_path"] for o in out}
            write_files_registry(out + [r for fp, r in registry.items() if fp not in done])
    done = {o["file_path"] for o in out}
    write_files_registry(out + [r for fp, r in registry.items() if fp not in done])
    return out
