"""Разбор HTML-отчётов (2000–2011 гг.) на синтетических страницах в CP1251."""

from datetime import date
from decimal import Decimal

from roskazna_kbs.htmlform import parse_html_report, parse_member_name

PAGE_2010 = """<html><head><meta http-equiv="content-type" content="text/html; charset=Windows-1251"></head><body>
<b><p align="center">Республика Башкортостан</p></b>
<b><p align="center">Отчет об исполнении консолидированного бюджета субъекта РФ и бюджета территориального
государственного внебюджетного фонда (бюджетная деятельность)</p></b>
<b><p align="center">на 01.01.2011</p></b>
<i><font size="-1">Единица измерения: руб.</i>
<b>Раздел: 1. Доходы</b>
<table bordercolor="#000000" border="1" width="600">
<tr><th>Наименование показателя</th><th>Код строки</th><th>АП</th><th>Подгруппа</th><th>КОСГУ</th>
<th>Утвержденные бюджетные назначения: бюджет субъекта РФ</th><th>Исполено: бюджет субъекта РФ</th></tr>
<tr><td>Доходы бюджета - ИТОГО</td><td>010</td><td>***</td><td>850</td><td>000</td><td>120858702258,11</td><td>125572020514,48</td></tr>
<tr><td>НАЛОГИ НА ПРИБЫЛЬ, ДОХОДЫ</td><td>010</td><td>000</td><td>101</td><td>000</td><td>51782674626,37</td><td></td></tr>
</table></body></html>"""

PAGE_2000 = """<html><head><title>Отчет</title></head><body>
<table border=0><tr><td><b><p align="center">Республика Башкортостан<br>Отчет об исполнении консолидированного
бюджета субъекта РФ<br>на 01.01.2001<br></p></b>
<table bordercolor="#000000" border="1" width="600">
<tr><th>Код отчетности</th><th>Наименование показателя</th><th>Консолидиро-<br>ванный бюджет субъекта РФ</th>
<th>Бюджет субъекта РФ</th><th>Местные бюджеты</th></tr>
<tr><td>01</td><td>НАЛОГОВЫЕ ДОХОДЫ</td><td>28591589</td><td>17564320</td><td>11027269</td></tr>
</table></td></tr></table></body></html>"""


def test_html_2010_report():
    r = parse_html_report(PAGE_2010.encode("cp1251"))
    h = r.header
    assert (h.form_code, h.report_date, h.budget_name, h.unit_multiplier) == (
        "0503317", date(2011, 1, 1), "Республика Башкортостан", 1)
    assert "activity:budget" in r.flags
    total, tax = r.lines
    assert total["is_total_marker"] and total["section"] == "income"
    assert tax["admin_code"] == "000" and tax["kbk_code"] == "101" and tax["kosgu_code"] == "000"
    got = {(v["line_no"], v["measure"], v["budget_level"]): v["value"] for v in r.values}
    assert got[(1, "approved", "subject")] == Decimal("120858702258.11")
    assert got[(1, "executed", "subject")] == Decimal("125572020514.48")  # «Исполено» — опечатка источника
    assert all(v["activity"] == "budget" for v in r.values)


def test_html_2000_single_table_thousands():
    r = parse_html_report(PAGE_2000.encode("cp1251"))
    assert r.header.form_code == "0524100" and r.header.unit_multiplier == 1000
    assert "unit_assumed_thousand" in r.flags
    (line,) = r.lines
    assert line["section"] == "summary" and line["line_code"] == "01"
    got = {(v["measure"], v["budget_level"]): v["value"] for v in r.values}
    assert got[("executed", "cons_subject")] == Decimal("28591589000.00")
    assert got[("executed", "local_total")] == Decimal("11027269000.00")


def test_html_member_names():
    assert parse_member_name("M12/Mes_428vy_01.html")["activity"] == "income_generating"
    assert parse_member_name("M12/Mes_430y_77.html")["form_code"] == "0503320"
    assert parse_member_name("0107/Mes0.html")["territory_code"] == "00"


PAGE_2020 = """<html><body><table border=0 width="600"><tr><td>
<b><p align="center">г. Москва (ЦАФК)</p></b>
<b><p align="center">Отчет об исполнении консолидированного бюджета субъекта Российской Федерации и бюджета
территориального государственного внебюджетного фонда</p></b>
<b><p align="center">на 01.03.2020</p></b> <i>Единица измерения: руб.</i>
<b>1. Доходы бюджета</b>
<table bordercolor="#000000" border="1" width="600">
<tr><th>Наименование показателя</th><th>Код строки</th><th>АП</th><th>Подгруппа</th><th>Подвид</th><th>КОСГУ</th>
<th>Утвержденные бюджетные назначения: суммы подлежащие исключению в рамках консолидированного бюджета субъекта РФ и бюджета ТГВФ</th>
<th>Исполнено: бюджет субъекта РФ</th></tr>
<tr><td>Доходы бюджета - всего</td><td>010</td><td>***</td><td>850</td><td>0000</td><td>000</td><td>108291887908.00</td><td>1223054464353.57</td></tr>
</table></td></tr></table></body></html>"""


def test_html_2012_plus_sections_without_razdel():
    r = parse_html_report(PAGE_2020.encode("cp1251"))
    assert [s["section"] for s in r.sheets] == ["income"]
    (line,) = r.lines
    assert line["is_total_marker"] and line["kbk_code"] == "8500000"
    got = {(v["measure"], v["budget_level"]): v["value"] for v in r.values}
    assert got == {("approved", "cons_subject_tgvf_elim"): Decimal("108291887908.00"),
                   ("executed", "subject"): Decimal("1223054464353.57")}
