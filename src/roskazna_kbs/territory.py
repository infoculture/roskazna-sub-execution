"""Коды территорий.

Казначейство нумерует территории собственным двузначным кодом (01 —
Республика Башкортостан, 26 — Белгородская область, 73 — г. Москва …). Код не
совпадает ни с ОКАТО/ОКТМО, ни с конституционной нумерацией субъектов.

Источники соответствия «код → наименование»:

* имя книги в пакете: ``NN_ДД.ММ.ГГГГ_<форма>.XLS``;
* ``TerrList_428m.html`` из HTML-архива (ссылки ``Mes_428m_NN.html``), CP1251;
* строки «шахматок» («26 - Белгородская область») — там же состав
  федеральных округов;
* наименование бюджета в шапке отчёта.

Код **95** — сводный отчёт по всем субъектам РФ (итог по РФ), см. ``SUMMARY_CODE``.
"""

from __future__ import annotations

import html
import re

# Варианты имён книг территорий:
#   01_01.09.2026_0503317.XLS          — с 2018 г.
#   СВОД_01.01.2019_0503317.XLS        — сводный отчёт (2017–2018 гг.)
#   0503317_01_01072012_M.XLS          — месячные пакеты 2012–2019 гг.
#   0503317-3_FO_001-2582480.XLS       — 2012–2017 гг.: код территории из трёх цифр
#   0503317_SVOD_01012017_Y.xls        — сводный отчёт (2014–2017 гг.)
#   0503323_010114.xls                 — сводный отчёт (2013 г.)
#   0503320_сводный.xls, 0503317)сводный.xls — сводный отчёт (до 2012 г.)
#   428 свод год 2011.xls              — сводный отчёт (2011 г.)
MEMBER_PATTERNS = [
    # свод проверяется первым: «428 свод год 2011.xls» начинается с номера формы, а не территории
    re.compile(r"(?:^|[_)\s(])(?P<svod>свод\w*|svod)(?:[_.\s]|$)", re.I),
    re.compile(r"^(?P<code>\d{2})[_\-. ]"),
    # 0503317_01_01072012_M.XLS — месячные пакеты 2012–2019 гг.
    re.compile(r"^0503\d{3}_(?P<code>\d{2,3})_\d{8}", re.I),
    re.compile(r"^(?P<code>\d{3})_"),
    re.compile(r"_FO_(?P<code>\d{2,3})(?:\D|$)", re.I),
    # 002001.xls — отчёт 2000–2004 гг.: 3 знака типа отчёта + 3 знака кода территории
    re.compile(r"^\d{3}(?P<code>\d{3})\.xlsx?$", re.I),
    # 0503323_010114.xls, 0503317_01012014.xls — сводный отчёт без слова «свод» (до 2013 г.)
    re.compile(r"^(?P<svod>0503\d{3})_\d{6,8}(?:_[A-Z])?\.xlsx?$", re.I),
]

# Код 95 — «Сводный отчет по консолидированным бюджетам субъектов Российской Федерации
# и территориальным государственным внебюджетным фондам» (итог по РФ). В части лет файл
# назван «СВОД_<дата>_<форма>.XLS» или «<форма>_SVOD_<дата>.xls».
SUMMARY_CODE = "95"
TERRLIST_RE = re.compile(r"Mes(?:_\w+?_)?(?P<code>\d+)\.html?\"?[^>]*>(?P<name>[^<]+)<", re.I)


def territory_from_member_name(name: str) -> str | None:
    for rx in MEMBER_PATTERNS:
        m = rx.search(name)
        if not m:
            continue
        if m.groupdict().get("svod"):
            return SUMMARY_CODE
        code = str(int(m.group("code"))).zfill(2)
        # 00_01.03.2020_0503317.XLS, Mes_428m_00.html — тот же сводный отчёт по РФ, что и 95
        return SUMMARY_CODE if code == "00" else code
    return None


def decode_html(payload: bytes) -> str:
    head = payload[:2048].lower()
    m = re.search(rb"charset=[\"']?([a-z0-9_-]+)", head)
    enc = m.group(1).decode() if m else "cp1251"
    try:
        return payload.decode(enc)
    except (LookupError, UnicodeDecodeError):
        return payload.decode("cp1251", errors="replace")


def parse_terrlist(payload: bytes) -> list[tuple[str, str]]:
    text = decode_html(payload)
    out = []
    for m in TERRLIST_RE.finditer(text):
        name = re.sub(r"\s+", " ", html.unescape(m.group("name"))).strip()
        if name:
            out.append((m.group("code").zfill(2), name))
    return out
