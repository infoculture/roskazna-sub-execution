"""Чтение ZIP-пакетов Казначейства и реестр вложенных файлов.

Особенности архивов:

* имена членов на русском записаны в CP866 без флага UTF-8 (бит 0x800), поэтому
  ``zipfile`` по умолчанию показывает их как CP437-«кракозябры»;
* пакет содержит вложенные ZIP (Excel, HTML, «по графам») — они читаются рекурсивно;
  в пакетах до 2012 г. «шахматки» упакованы в RAR (нужен ``unrar`` или ``bsdtar``);
* формат члена определяется по сигнатуре, а не по расширению: книги «по графам»
  — это XLSX с расширением ``.xls``.

Архивы **не распаковываются на диск**: распакованный архив за все годы занял бы
в несколько раз больше места, чем ZIP. Парсеры читают байты членов прямо из
архива через :func:`iter_members`. Для ручного изучения есть
:func:`extract_to_dir` (команда ``extract``).

Реестр — ``data/catalog/members.csv``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import zipfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass, fields
from pathlib import Path, PurePosixPath

from .config import DATA_DIR, FILES_CSV, MEMBERS_CSV

log = logging.getLogger(__name__)


@dataclass
class Member:
    member_id: str          # <file_id>:<idx>[:<idx>...] — путь индексов во вложенных архивах
    file_id: str
    parent_member_id: str
    depth: int
    member_index: int
    member_path: str        # путь внутри пакета: «внутренний.zip/файл.xls»
    member_name: str        # имя файла (декодированное)
    member_name_raw: str    # имя как его отдаёт zipfile
    name_encoding: str
    extension: str
    detected_format: str
    size_bytes: int
    sha256: str
    zip_datetime: str


def decode_member_name(info: zipfile.ZipInfo) -> tuple[str, str]:
    """Возвращает (читаемое имя, использованная кодировка)."""
    if info.flag_bits & 0x800:
        return info.filename, "utf-8"
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename, "unknown"
    if all(b < 0x80 for b in raw):
        return info.filename, "ascii"
    for enc in ("cp866", "cp1251"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return info.filename, "cp437"


def sniff_format(data: bytes, name: str) -> str:
    head = data[:8]
    ext = PurePosixPath(name).suffix.lower()
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        # OLE2-контейнер: XLS (BIFF5/8) или DOC.
        probe = data[: 1 << 16]
        if b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k" in probe or b"B\x00o\x00o\x00k" in probe:
            return "xls"
        if b"W\x00o\x00r\x00d\x00D\x00o\x00c" in probe or ext == ".doc":
            return "doc"
        return "xls" if ext == ".xls" else "ole2"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
        except zipfile.BadZipFile:
            return "zip-broken"
        if "xl/workbook.bin" in names:
            return "xlsb"  # двоичная книга Excel 2007+ («Информация об АД …», 2022 г.)
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        if any(n.startswith("word/") for n in names):
            return "docx"
        return "zip"
    if head.startswith(b"Rar!"):
        return "rar"
    if head.startswith(b"7z\xbc\xaf"):
        return "7z"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if data[:4] in (b"\x09\x08\x10\x00", b"\x09\x04\x06\x00", b"\x09\x02\x06\x00", b"\x09\x00\x04\x00"):
        return "xls"  # BIFF2–BIFF4 без OLE-контейнера
    lowered = data[:4096].lower()
    if b"<?xml" in lowered and b"urn:schemas-microsoft-com:office:spreadsheet" in data[:65536].lower():
        return "xml-spreadsheet"
    if b"<html" in lowered or b"<table" in lowered or b"<!doctype html" in lowered:
        return "html"
    if ext in (".css", ".txt", ".csv"):
        return ext.lstrip(".")
    return ext.lstrip(".") or "unknown"


def _children(data: bytes, fmt: str) -> Iterator[tuple[int, str, str, str, bytes, str]]:
    """Члены архива: (индекс, имя, имя как в архиве, кодировка имени, содержимое, дата)."""
    if fmt == "rar":
        import rarfile  # нужен внешний unrar или bsdtar

        with rarfile.RarFile(io.BytesIO(data)) as archive:
            for idx, info in enumerate(archive.infolist()):
                if info.is_dir():
                    continue
                dt = "%04d-%02d-%02dT%02d:%02d:%02d" % info.date_time if info.date_time else ""
                yield idx, info.filename, info.filename, "rar", archive.read(info), dt
        return
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for idx, info in enumerate(archive.infolist()):
            if info.is_dir():
                continue
            name, enc = decode_member_name(info)
            yield idx, name, info.filename, enc, archive.read(info), "%04d-%02d-%02dT%02d:%02d:%02d" % info.date_time


def _walk(data: bytes, file_id: str, parent: str, prefix_ids: str, prefix_path: str, depth: int,
          container: str = "zip") -> Iterator[tuple[Member, bytes]]:
    for idx, name, raw_name, enc, payload, dt in _children(data, container):
        if True:
            fmt = sniff_format(payload, name)
            member_id = f"{file_id}:{prefix_ids}{idx}"
            member_path = f"{prefix_path}{name}"
            m = Member(
                member_id=member_id,
                file_id=file_id,
                parent_member_id=parent,
                depth=depth,
                member_index=idx,
                member_path=member_path,
                member_name=PurePosixPath(name.replace("\\", "/")).name,
                member_name_raw=raw_name,
                name_encoding=enc,
                extension=PurePosixPath(name).suffix.lower(),
                detected_format=fmt,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                zip_datetime=dt,
            )
            yield m, payload
            if fmt in ("zip", "rar"):
                try:
                    yield from _walk(payload, file_id, member_id, f"{prefix_ids}{idx}:", f"{member_path}/", depth + 1, fmt)
                except Exception as exc:  # noqa: BLE001 — битый вложенный архив не должен ронять обход
                    log.error("bad nested archive %s: %s", member_path, exc)


def iter_members(file_row: dict) -> Iterator[tuple[Member, bytes]]:
    """Все члены пакета (рекурсивно) с их содержимым."""
    path = DATA_DIR / file_row["local_path"]
    data = path.read_bytes()
    fmt = sniff_format(data, path.name)
    if fmt not in ("zip", "rar"):
        # Пакет оказался не архивом — регистрируем файл как единственный член.
        yield Member(
            member_id=f"{file_row['file_id']}:0", file_id=file_row["file_id"], parent_member_id="",
            depth=0, member_index=0, member_path=path.name, member_name=path.name,
            member_name_raw=path.name, name_encoding="", extension=path.suffix.lower(),
            detected_format=fmt, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
            zip_datetime="",
        ), data
        return
    # Часть пакетов — RAR с расширением .zip (KBSRF-na-01.02.2020.zip).
    yield from _walk(data, file_row["file_id"], "", "", "", 0, fmt)


def read_files(path: Path = FILES_CSV) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r.get("sha256")]


def inventory_all() -> list[Member]:
    rows: list[Member] = []
    files = read_files()
    for i, f in enumerate(files, 1):
        try:
            rows.extend(m for m, _ in iter_members(f))
        except (zipfile.BadZipFile, OSError) as exc:
            log.error("cannot read %s: %s", f["local_path"], exc)
        if i % 20 == 0:
            log.info("inventory %s/%s", i, len(files))
    write_members(rows)
    return rows


def write_members(rows: list[Member], path: Path = MEMBERS_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[f.name for f in fields(Member)])
        writer.writeheader()
        for m in rows:
            writer.writerow(asdict(m))


def read_members(path: Path = MEMBERS_CSV) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _safe_relpath(name: str) -> Path:
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".", "..", "/")]
    return Path(*parts) if parts else Path("unnamed")


def extract_to_dir(file_row: dict, out_dir: Path) -> int:
    """Распаковывает пакет (с вложенными архивами) для ручного изучения."""
    n = 0
    for m, payload in iter_members(file_row):
        if m.detected_format in ("zip", "rar"):
            continue
        target = out_dir / _safe_relpath(m.member_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        n += 1
    return n
