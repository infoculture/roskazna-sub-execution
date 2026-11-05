"""Обход HTML-каталога «Исполнение бюджетов → Консолидированные бюджеты субъектов РФ».

Страница построена на Laravel Livewire. Компонент со списком файлов синхронизирует
фильтры с адресной строкой (``queryString``), поэтому каталог обходится обычными
GET-запросами::

    ?filter_type=<48|49>&filter_year=<YYYY>&page=<N>

``48`` — ежемесячные срезы, ``49`` — годовые. На странице по 10 карточек.
Каждая карточка содержит заголовок («На 1 августа 2026», «2025 год»), путь к ZIP
(``/storage/page-files/512/...``) и показанную порталом дату публикации.

Результат — ``data/catalog/catalog.csv``: одна строка на карточку каталога
(тип × год фильтра × позиция). Одна и та же ZIP-ссылка может встречаться в
нескольких карточках; дедупликация выполняется на этапе загрузки.
"""

from __future__ import annotations

import csv
import html
import json
import logging
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .config import BASE_URL, CACHE_DIR, CATALOG_CSV, CATALOG_URL, PUBLICATION_TYPES
from .http import get_with_retry, make_session

log = logging.getLogger(__name__)

TITLE_RE = re.compile(r'<h3 class="h3-nd-card[^"]*" title="(?P<title>[^"]*)"')
FILE_RE = re.compile(r"downloadFile\('(?P<path>[^']+)'\)")
HREF_RE = re.compile(r'<a href="(?P<href>[^"#]+\.(?:zip|rar|7z|xlsx?|pdf))#?"', re.I)
DATE_RE = re.compile(r'<p class="date-doc">(?P<date>[^<]*)</p>')
SNAPSHOT_RE = re.compile(r'wire:snapshot="(?P<snap>[^"]*)"')
NEXT_RE = re.compile(r"wire:click=\"nextPage\('page'\)\"")

RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


@dataclass
class CatalogCard:
    publication_type_id: int
    publication_type: str
    filter_year: int
    page: int
    position: int
    card_title: str
    file_path: str
    url: str
    published_at_raw: str
    published_at: str
    retrieved_at: str


def parse_ru_datetime(text: str) -> str:
    """'1 октября 2026 09:00' → '2026-10-01T09:00'. Пустая строка, если не распознано."""
    m = re.match(r"\s*(\d{1,2})\s+(\S+)\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", text or "")
    if not m or m.group(2).lower() not in RU_MONTHS:
        m2 = re.match(r"\s*(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", text or "")
        if not m2:
            return ""
        day, month, year = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        hh, mm = int(m2.group(4) or 0), int(m2.group(5) or 0)
    else:
        day, month, year = int(m.group(1)), RU_MONTHS[m.group(2).lower()], int(m.group(3))
        hh, mm = int(m.group(4) or 0), int(m.group(5) or 0)
    try:
        return datetime(year, month, day, hh, mm).isoformat(timespec="minutes")
    except ValueError:
        return ""


def file_url(path: str) -> str:
    path = path.lstrip("/")
    return f"{BASE_URL}/{quote(path, safe='/._-()')}"


def parse_snapshots(page_html: str) -> list[dict]:
    out = []
    for m in SNAPSHOT_RE.finditer(page_html):
        try:
            out.append(json.loads(html.unescape(m.group("snap"))))
        except json.JSONDecodeError:
            continue
    return out


def files_snapshot(page_html: str) -> dict:
    """Снимок Livewire-компонента, у которого есть фильтры типа и года."""
    for snap in parse_snapshots(page_html):
        data = snap.get("data", {})
        if "filter_type" in data or "years" in data:
            return snap
    return {}


def years_from_snapshot(snap: dict) -> list[int]:
    """Список лет фильтра. Livewire сериализует массив как [[...], {"s": "arr"}]."""
    years = snap.get("data", {}).get("years")
    if isinstance(years, list) and years and isinstance(years[0], list):
        years = years[0]
    out = []
    for y in years or []:
        try:
            out.append(int(y))
        except (TypeError, ValueError):
            continue
    return out


def parse_cards(page_html: str) -> list[dict]:
    start = page_html.find('<div class="documents-columns">')
    chunk = page_html[start:] if start >= 0 else page_html
    pieces = re.split(r'(?=<div class="card news-card)', chunk)[1:]
    cards = []
    for piece in pieces:
        title = TITLE_RE.search(piece)
        path = FILE_RE.search(piece) or HREF_RE.search(piece)
        if not path:
            continue
        date = DATE_RE.search(piece)
        raw_path = html.unescape(path.group(1)).split("#")[0]
        if raw_path.startswith(BASE_URL):
            raw_path = raw_path[len(BASE_URL):]
        cards.append(
            {
                "card_title": html.unescape(title.group("title")).strip() if title else "",
                "file_path": raw_path.lstrip("/"),
                "published_at_raw": html.unescape(date.group("date")).strip() if date else "",
            }
        )
    return cards


def _save_html(type_id: int, year: int | None, page: int, text: str) -> None:
    """Сохраняет страницы каталога в кеш — для отладки парсера и воспроизводимости."""
    d = CACHE_DIR / "catalog_html"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"type{type_id}_{year or 'default'}_p{page}.html").write_text(text, encoding="utf-8")


def crawl(max_pages: int = 50) -> list[CatalogCard]:
    session = make_session()
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result: list[CatalogCard] = []
    for type_id, (type_code, type_label) in PUBLICATION_TYPES.items():
        first = get_with_retry(session, CATALOG_URL, params={"filter_type": type_id})
        first.raise_for_status()
        _save_html(type_id, None, 1, first.text)
        years = years_from_snapshot(files_snapshot(first.text))
        if not years:
            raise RuntimeError(f"Не удалось прочитать список лет для filter_type={type_id}")
        log.info("type %s (%s): years %s", type_id, type_label, years)
        for year in years:
            for page in range(1, max_pages + 1):
                params = {"filter_type": type_id, "filter_year": year}
                if page > 1:
                    params["page"] = page
                resp = get_with_retry(session, CATALOG_URL, params=params)
                resp.raise_for_status()
                _save_html(type_id, year, page, resp.text)
                state = files_snapshot(resp.text).get("data", {})
                if str(state.get("filter_type")) != str(type_id) or str(state.get("filter_year")) != str(year):
                    log.warning("filter state mismatch for %s: %s/%s", params, state.get("filter_type"), state.get("filter_year"))
                cards = parse_cards(resp.text)
                for pos, card in enumerate(cards, start=1):
                    result.append(
                        CatalogCard(
                            publication_type_id=type_id,
                            publication_type=type_code,
                            filter_year=year,
                            page=page,
                            position=pos,
                            card_title=card["card_title"],
                            file_path=card["file_path"],
                            url=file_url(card["file_path"]),
                            published_at_raw=card["published_at_raw"],
                            published_at=parse_ru_datetime(card["published_at_raw"]),
                            retrieved_at=retrieved_at,
                        )
                    )
                log.info("  %s %s p%s: %s cards", type_code, year, page, len(cards))
                if not NEXT_RE.search(resp.text):
                    break
    return result


def write_catalog(cards: list[CatalogCard], path: Path = CATALOG_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[f.name for f in fields(CatalogCard)])
        writer.writeheader()
        for card in cards:
            writer.writerow(asdict(card))


def read_catalog(path: Path = CATALOG_CSV) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
