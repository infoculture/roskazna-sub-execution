from roskazna_kbs.catalog import file_url, parse_cards, parse_ru_datetime, years_from_snapshot

CARD_HTML = """
<div class="documents-columns">
<div class="card news-card card-notmain" data-content-files="">
    <div class="card-header card-header-news">
        <a href="/storage/page-files/512/КБСРФ на 01.09.2026.zip#" target="_blank" class="card-body news-card-body">
            <h3 class="h3-nd-card tdn" title="на 1 сентября 2026">на 1 сентября 2026</h3>
        </a>
    </div>
    <div class="spaceb-content crad-footer-news">
        <div class="doc-download">
            <a  href="#" wire:click.prevent="downloadFile('storage/page-files/512/КБСРФ на 01.09.2026.zip')"  class="icon-zip">zip</a>
        </div>
        <p class="date-doc">7 октября 2026 09:00</p>
    </div>
</div>
<div class="card news-card card-notmain" data-content-files="">
    <a href="/storage/page-files/512/KBSRF-na-01.04.2005.zip#" target="_blank" class="card-body news-card-body">
        <h3 class="h3-nd-card tdn" title="на 1 апреля 2005">на 1 апреля 2005</h3>
    </a>
    <p class="date-doc">1 января 2016 00:00</p>
</div>
</div>
"""


def test_parse_cards():
    cards = parse_cards(CARD_HTML)
    assert cards == [
        {
            "card_title": "на 1 сентября 2026",
            "file_path": "storage/page-files/512/КБСРФ на 01.09.2026.zip",
            "published_at_raw": "7 октября 2026 09:00",
        },
        {
            "card_title": "на 1 апреля 2005",
            "file_path": "storage/page-files/512/KBSRF-na-01.04.2005.zip",
            "published_at_raw": "1 января 2016 00:00",
        },
    ]


def test_parse_ru_datetime():
    assert parse_ru_datetime("7 октября 2026 09:00") == "2026-10-07T09:00"
    assert parse_ru_datetime("30 мая 2026") == "2026-05-30T00:00"
    assert parse_ru_datetime("01.02.2026 10:15") == "2026-02-01T10:15"
    assert parse_ru_datetime("") == ""
    assert parse_ru_datetime("вчера") == ""


def test_file_url_quotes_cyrillic_and_spaces():
    assert file_url("storage/page-files/512/КБСРФ на 01.09.2026.zip") == (
        "https://roskazna.gov.ru/storage/page-files/512/"
        "%D0%9A%D0%91%D0%A1%D0%A0%D0%A4%20%D0%BD%D0%B0%2001.09.2026.zip"
    )


def test_years_from_livewire_snapshot():
    snap = {"data": {"years": [[2026, 2025, "2000"], {"s": "arr"}]}}
    assert years_from_snapshot(snap) == [2026, 2025, 2000]
