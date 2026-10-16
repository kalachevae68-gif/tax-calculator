"""Калькулятор налоговой нагрузки: ИП и ООО, УСН / ОСНО / АУСН. Правила 2026 года.

Запуск: streamlit run app.py
"""
import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Параметры 2026 года (сверяйте с актуальными НПА перед использованием)
# ---------------------------------------------------------------------------
IP_FIXED = 57_390            # фиксированные взносы ИП за себя
IP_1PCT_THRESHOLD = 300_000  # 1% с дохода свыше этой суммы
IP_1PCT_CAP = 321_818        # максимум взноса 1%

NDFL_SCALE = [(2_400_000, 0.13), (5_000_000, 0.15), (20_000_000, 0.18),
              (50_000_000, 0.20), (math.inf, 0.22)]
DIV_SCALE = [(2_400_000, 0.13), (math.inf, 0.15)]

VAT = 0.22                   # общая ставка НДС
USN_VAT_FREE = 20_000_000    # до этого дохода УСН освобождена от НДС
USN_VAT5_LIMIT = 272_500_000 # до этого дохода — НДС 5%, выше — 7%
USN_LIMIT = 490_500_000      # лимит дохода для УСН
USN_STAFF = 130              # лимит численности для УСН

AUSN_LIMIT = 60_000_000
AUSN_STAFF = 5
AUSN_MIN = 0.03              # минимальный налог на АУСН «Доходы минус расходы»

INS_BASE = 2_979_000         # предельная база по страховым взносам на 1 сотрудника
MROT = 27_093
INJURY = 0.002               # взносы «на травматизм», минимальный класс риска

COMPONENTS = ["Налог", "НДС", "Взносы ИП за себя", "Взносы за сотрудников",
              "НДФЛ с дивидендов", "Потери без НДС"]


def rub(x: float) -> str:
    return f"{x:,.0f} ₽".replace(",", " ")


def progressive(amount: float, scale) -> float:
    """Налог по прогрессивной шкале."""
    tax, lower = 0.0, 0.0
    for upper, rate in scale:
        if amount <= lower:
            break
        tax += (min(amount, upper) - lower) * rate
        lower = upper
    return tax


def ip_own_contrib(base_1pct: float) -> float:
    """Взносы ИП за себя: фиксированная часть + 1% свыше 300 000 ₽."""
    extra = min(max(base_1pct - IP_1PCT_THRESHOLD, 0) * 0.01, IP_1PCT_CAP)
    return IP_FIXED + extra


def staff_contrib(payroll: float, staff: int, msp: bool) -> float:
    """Взносы с ФОТ: считаем по средней зарплате на сотрудника."""
    if payroll <= 0 or staff <= 0:
        return 0.0
    per = payroll / staff
    if msp:
        threshold = 1.5 * MROT * 12
        main = min(per, threshold) * 0.30 + max(per - threshold, 0) * 0.15
    else:
        main = min(per, INS_BASE) * 0.30 + max(per - INS_BASE, 0) * 0.151
    return (main + per * INJURY) * staff


# ---------------------------------------------------------------------------
# Расчёт режимов
# ---------------------------------------------------------------------------
def result(form, regime, comps, steps, problems, p):
    total = sum(comps.values())
    return {
        "Форма": form,
        "Режим": regime,
        "Название": f"{form} · {regime}",
        **comps,
        "Итого": total,
        "% от дохода": total / p["income"] * 100 if p["income"] else 0.0,
        "Прибыль до налогов": p["income"] - p["expenses"] - p["payroll"],
        "Чистая прибыль": p["income"] - p["expenses"] - p["payroll"] - total + comps["НДФЛ с дивидендов"],
        "Собственнику": p["income"] - p["expenses"] - p["payroll"] - total,
        "Доступен": not problems,
        "Причины": problems,
        "Шаги": steps,
    }


def vat_loss(p, vat_rate: float) -> float:
    """Скидка, которую потребуют клиенты-плательщики НДС за отсутствие вычета."""
    if not p["clients_vat"]:
        return 0.0
    return p["income"] * p["vat_discount"] / 100 * (1 - vat_rate / VAT)


def calc_usn(p, form, obj, vat_mode):
    """obj: 'Д' или 'ДР'; vat_mode: None (освобождение), 'reduced' (5/7%), 'full' (22%)."""
    income, expenses, payroll, staff = p["income"], p["expenses"], p["payroll"], p["staff"]
    comps = dict.fromkeys(COMPONENTS, 0.0)
    steps, problems = [], []

    if vat_mode is None:
        inc, exp, vat_rate = income, expenses, 0.0
        if income > USN_VAT_FREE:
            problems.append(f"доход выше {rub(USN_VAT_FREE)} — освобождение от НДС не действует")
        title_vat = ""
    elif vat_mode == "reduced":
        vat_rate = 0.05 if income <= USN_VAT5_LIMIT else 0.07
        comps["НДС"] = income * vat_rate / (1 + vat_rate)
        inc, exp = income - comps["НДС"], expenses
        steps.append(f"НДС {vat_rate:.0%} без вычетов: {rub(income)} × {vat_rate:.0%}/{1 + vat_rate:.2f} = {rub(comps['НДС'])}")
        title_vat = f" + НДС {vat_rate:.0%}"
    else:
        vat_rate = VAT
        out_vat = income * VAT / (1 + VAT)
        comps["НДС"] = out_vat - p["input_vat"]
        inc, exp = income - out_vat, expenses - p["input_vat"]
        steps.append(f"НДС 22%: начислено {rub(out_vat)} − вычет {rub(p['input_vat'])} = {rub(comps['НДС'])}")
        title_vat = " + НДС 22%"

    if income > USN_LIMIT:
        problems.append(f"доход выше лимита УСН {rub(USN_LIMIT)}")
    if staff > USN_STAFF:
        problems.append(f"сотрудников больше {USN_STAFF}")

    sc = staff_contrib(payroll, staff, p["msp"])
    comps["Взносы за сотрудников"] = sc
    if sc:
        steps.append(f"Взносы с ФОТ {rub(payroll)}: {rub(sc)}")

    rate_d, mark_d, rate_dr, mark_dr = p["usn_rates"][form]

    if obj == "Д":
        rate = rate_d / 100
        base_tax = inc * rate
        steps.append(f"Налог до уменьшения: {rub(inc)} × {rate * 100:g}% = {rub(base_tax)}")
        if form == "ИП":
            own = ip_own_contrib(inc)
            comps["Взносы ИП за себя"] = own
            steps.append(f"Взносы ИП за себя: {rub(own)}")
            if staff > 0:
                reduce = min(own + sc, base_tax * 0.5)
                steps.append(f"Есть сотрудники — налог уменьшается на взносы не более чем на 50%: −{rub(reduce)}")
            else:
                reduce = min(own, base_tax)
                steps.append(f"Без сотрудников налог уменьшается на взносы полностью: −{rub(reduce)}")
        else:
            reduce = min(sc, base_tax * 0.5)
            if reduce:
                steps.append(f"Уменьшение на взносы за сотрудников (не более 50%): −{rub(reduce)}")
        comps["Налог"] = base_tax - reduce
        regime = f"УСН «Доходы» {rate_d:g}%{mark_d}{title_vat}"
    else:
        rate = rate_dr / 100
        own = 0.0
        if form == "ИП":
            own = ip_own_contrib(inc - exp - payroll - sc)
            comps["Взносы ИП за себя"] = own
            steps.append(f"Взносы ИП за себя: {rub(own)}")
        base = inc - exp - payroll - sc - own
        tax = max(base, 0) * rate
        min_tax = inc * 0.01
        steps.append(f"База: {rub(inc)} − расходы {rub(exp)} − ФОТ {rub(payroll)} − взносы {rub(sc + own)} = {rub(base)}")
        if tax < min_tax:
            steps.append(f"Налог {rub(tax)} меньше минимального (1% дохода) — платится {rub(min_tax)}")
            tax = min_tax
        else:
            steps.append(f"Налог: {rub(max(base, 0))} × {rate * 100:g}% = {rub(tax)}")
        comps["Налог"] = tax
        regime = f"УСН «Д−Р» {rate_dr:g}%{mark_dr}{title_vat}"

    if form == "ООО":
        profit = inc - exp - payroll - sc - comps["Налог"]
        comps["НДФЛ с дивидендов"] = progressive(max(profit, 0), DIV_SCALE)
        steps.append(f"Чистая прибыль к выплате {rub(max(profit, 0))}, НДФЛ с дивидендов {rub(comps['НДФЛ с дивидендов'])}")

    comps["Потери без НДС"] = vat_loss(p, vat_rate)
    if comps["Потери без НДС"]:
        steps.append(f"Скидка клиентам за отсутствие полного вычета НДС: {rub(comps['Потери без НДС'])}")
    return result(form, regime, comps, steps, problems, p)


def calc_osno(p, form):
    income, payroll, staff = p["income"], p["payroll"], p["staff"]
    comps = dict.fromkeys(COMPONENTS, 0.0)
    out_vat = income * VAT / (1 + VAT)
    comps["НДС"] = out_vat - p["input_vat"]
    inc, exp = income - out_vat, p["expenses"] - p["input_vat"]
    steps = [f"НДС 22%: начислено {rub(out_vat)} − вычет {rub(p['input_vat'])} = {rub(comps['НДС'])}"]
    sc = staff_contrib(payroll, staff, p["msp"])
    comps["Взносы за сотрудников"] = sc
    profit = inc - exp - payroll - sc
    if form == "ИП":
        own = ip_own_contrib(profit)
        comps["Взносы ИП за себя"] = own
        base = max(profit - own, 0)
        comps["Налог"] = progressive(base, NDFL_SCALE)
        steps.append(f"Взносы ИП за себя: {rub(own)}")
        steps.append(f"НДФЛ по шкале 13–22% с {rub(base)} = {rub(comps['Налог'])}")
    else:
        comps["Налог"] = max(profit, 0) * 0.25
        comps["НДФЛ с дивидендов"] = progressive(max(profit - comps["Налог"], 0), DIV_SCALE)
        steps.append(f"Налог на прибыль 25% с {rub(max(profit, 0))} = {rub(comps['Налог'])}")
        steps.append(f"НДФЛ с дивидендов: {rub(comps['НДФЛ с дивидендов'])}")
    return result(form, "ОСНО", comps, steps, [], p)


def calc_ausn(p, form, obj):
    income, expenses, payroll, staff = p["income"], p["expenses"], p["payroll"], p["staff"]
    comps = dict.fromkeys(COMPONENTS, 0.0)
    problems = []
    if income > AUSN_LIMIT:
        problems.append(f"доход выше {rub(AUSN_LIMIT)}")
    if staff > AUSN_STAFF:
        problems.append(f"сотрудников больше {AUSN_STAFF}")
    steps = ["Взносы за сотрудников и фиксированные взносы ИП на АУСН не платятся"]
    if obj == "Д":
        comps["Налог"] = income * 0.08
        steps.append(f"Налог: {rub(income)} × 8% = {rub(comps['Налог'])}")
        regime = "АУСН «Доходы» 8%"
    else:
        base = income - expenses - payroll
        tax = max(base, 0) * 0.20
        min_tax = income * AUSN_MIN
        steps.append(f"База: {rub(income)} − расходы {rub(expenses)} − ФОТ {rub(payroll)} = {rub(base)}")
        if tax < min_tax:
            steps.append(f"Налог {rub(tax)} меньше минимального (3% дохода) — платится {rub(min_tax)}")
            tax = min_tax
        else:
            steps.append(f"Налог: {rub(max(base, 0))} × 20% = {rub(tax)}")
        comps["Налог"] = tax
        regime = "АУСН «Д−Р» 20%"
    if form == "ООО":
        profit = income - expenses - payroll - comps["Налог"]
        comps["НДФЛ с дивидендов"] = progressive(max(profit, 0), DIV_SCALE)
        steps.append(f"НДФЛ с дивидендов: {rub(comps['НДФЛ с дивидендов'])}")
    comps["Потери без НДС"] = vat_loss(p, 0.0)
    return result(form, regime, comps, steps, problems, p)


def calc_all(p):
    rows = []
    for form in ("ИП", "ООО"):
        if p["income"] <= USN_VAT_FREE:
            rows += [calc_usn(p, form, "Д", None), calc_usn(p, form, "ДР", None)]
        else:
            for mode in ("reduced", "full"):
                rows += [calc_usn(p, form, "Д", mode), calc_usn(p, form, "ДР", mode)]
        rows.append(calc_osno(p, form))
        rows += [calc_ausn(p, form, "Д"), calc_ausn(p, form, "ДР")]
    return rows


def warnings(p, best):
    w = []
    if p["staff"] > AUSN_STAFF or p["income"] > AUSN_LIMIT:
        w.append(("error", f"АУСН недоступна: нужно не больше {AUSN_STAFF} сотрудников и доход до {rub(AUSN_LIMIT)} в год."))
    else:
        w.append(("info", "АУСН действует не во всех регионах, требует зарплату только безналом и банк-партнёр. "
                          "Взносы «на травматизм» на АУСН платятся фиксированной суммой и в расчёт не включены."))
    if p["income"] > USN_VAT_FREE:
        w.append(("warning", f"Доход выше {rub(USN_VAT_FREE)}: на УСН придётся платить НДС — "
                             f"5% (до {rub(USN_VAT5_LIMIT)}) или 7% без вычетов, либо 22% с вычетами. "
                             "Выбор пониженной ставки фиксируется на 3 года."))
    if p["income"] > USN_LIMIT:
        w.append(("error", f"Доход выше лимита УСН {rub(USN_LIMIT)} — доступна только ОСНО."))
    if p["staff"] > USN_STAFF:
        w.append(("error", f"Больше {USN_STAFF} сотрудников — УСН недоступна."))
    if p["staff"] and p["payroll"] / p["staff"] < MROT * 12:
        w.append(("warning", f"Средняя зарплата ниже МРОТ ({rub(MROT)} в месяц) — проверьте ФОТ."))
    if p["payroll"] and not p["staff"]:
        w.append(("warning", "Указан ФОТ, но число сотрудников 0 — взносы с ФОТ не посчитаны."))
    if p["expenses"] + p["payroll"] > p["income"]:
        w.append(("warning", "Расходы с зарплатой больше выручки — бизнес в убытке, на УСН «Д−Р» платится минимальный налог."))
    if p["clients_vat"] and best is not None and best["НДС"] == 0:
        w.append(("warning", "Клиентам важен входящий НДС, а самый выгодный режим работает без НДС. "
                             "Проверьте, готовы ли клиенты работать без вычета при заданной скидке."))
    return w


# ---------------------------------------------------------------------------
# Названия и цвета частей налоговой нагрузки, выгрузка в Excel
# ---------------------------------------------------------------------------
CHART_NAMES = {
    "Налог": "Налог по системе",
    "НДС": "НДС к уплате",
    "Взносы ИП за себя": "Взносы ИП за себя",
    "Взносы за сотрудников": "Взносы за сотрудников",
    "НДФЛ с дивидендов": "НДФЛ с дивидендов",
    "Потери без НДС": "Скидка клиентам без НДС",
}
CHART_COLORS = {
    "Налог": "#FF8A00",
    "НДС": "#4A78C2",
    "Взносы ИП за себя": "#9B6BC4",
    "Взносы за сотрудников": "#2E9E8F",
    "НДФЛ с дивидендов": "#D9534F",
    "Потери без НДС": "#9E9E9E",
}
XLSX_COLUMNS = [
    ("Название", "Вариант"),
    ("Налог", "Налог по системе (УСН / НДФЛ / на прибыль), ₽"),
    ("НДС", "НДС к уплате, ₽"),
    ("Взносы ИП за себя", "Взносы ИП за себя, ₽"),
    ("Взносы за сотрудников", "Взносы за сотрудников, ₽"),
    ("НДФЛ с дивидендов", "НДФЛ с дивидендов собственника ООО, ₽"),
    ("Потери без НДС", "Скидка клиентам за работу без НДС, ₽"),
    ("Итого", "Налоговая нагрузка, ₽"),
    ("% от дохода", "Налоговая нагрузка, % от выручки"),
    ("Прибыль до налогов", "Прибыль до налогов, ₽"),
    ("Чистая прибыль", "Чистая прибыль после налогов, ₽"),
    ("Собственнику", "Чистая прибыль собственнику ООО (после НДФЛ с дивидендов), ₽"),
    ("Статус", "Доступен ли"),
]


def build_xlsx(p, rows, best) -> bytes:
    """Файл Excel: исходные данные, сравнение вариантов, расчёт по шагам."""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    money, pct = '#,##0 "₽"', '0.0%'
    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="FFF5EC")
    best_fill = PatternFill("solid", fgColor="C8F0D0")
    grey = Font(color="999999")
    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")

    wb = Workbook()

    # Лист 1. Исходные данные
    ws = wb.active
    ws.title = "Исходные данные"
    ws.append(["Расчёт налоговой нагрузки, правила 2026 года"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    rate_d, mark_d, rate_dr, mark_dr = p["usn_rates"]["ИП"]
    data = [
        ("Выручка за год (с НДС)", p["income"], money),
        ("Расходы за год", p["expenses"], money),
        ("в т.ч. входящий НДС в расходах", p["input_vat"], money),
        ("Количество сотрудников", p["staff"], "0"),
        ("Фонд оплаты труда за год", p["payroll"], money),
        ("Прибыль до налогов (выручка − расходы − зарплата)", p["income"] - p["expenses"] - p["payroll"], money),
        ("Ставка УСН «Доходы», %", rate_d, "0.0"),
        ("Ставка УСН «Доходы минус расходы», %", rate_dr, "0.0"),
        ("Льготный тариф взносов МСП", "да" if p["msp"] else "нет", None),
        ("Клиентам важен входящий НДС", "да" if p["clients_vat"] else "нет", None),
    ]
    if p["clients_vat"]:
        data.append(("Скидка клиентам без НДС, %", p["vat_discount"], "0"))
    for name, value, fmt in data:
        ws.append([name, value])
        if fmt:
            ws.cell(ws.max_row, 2).number_format = fmt
    if best:
        ws.append([])
        ws.append(["Самый выгодный вариант", best["Название"]])
        ws.append(["Налоговая нагрузка", best["Итого"]])
        ws.cell(ws.max_row, 2).number_format = money
        ws.append(["Чистая прибыль после налогов", best["Собственнику"]])
        ws.cell(ws.max_row, 2).number_format = money
        for r in range(ws.max_row - 2, ws.max_row + 1):
            ws.cell(r, 1).font = bold
            ws.cell(r, 2).font = bold
    ws.append([])
    ws.append(["Расчёт ориентировочный. Итоговое решение принимает бухгалтер с учётом всех обстоятельств."])
    ws.column_dimensions["A"].width = 52
    ws.column_dimensions["B"].width = 22

    # Лист 2. Сравнение
    ws = wb.create_sheet("Сравнение")
    ordered = sorted(rows, key=lambda r: (not r["Доступен"], r["Итого"]))
    cols = [(k, h) for k, h in XLSX_COLUMNS if k != "Потери без НДС" or any(r["Потери без НДС"] for r in rows)]
    ws.append([h for _, h in cols])
    for c in range(1, len(cols) + 1):
        cell = ws.cell(1, c)
        cell.font, cell.fill, cell.alignment, cell.border = bold, head_fill, wrap, border
    ws.row_dimensions[1].height = 60
    for r in ordered:
        line = []
        for k, _ in cols:
            if k == "% от дохода":
                line.append(r[k] / 100)
            elif k == "Статус":
                line.append("доступен" if r["Доступен"] else "недоступен: " + "; ".join(r["Причины"]))
            elif k == "Собственнику" and r["Форма"] != "ООО":
                line.append("—")
            else:
                line.append(r[k])
        ws.append(line)
        row = ws.max_row
        for c, (k, _) in enumerate(cols, start=1):
            cell = ws.cell(row, c)
            cell.border = border
            if k == "% от дохода":
                cell.number_format = pct
            elif k not in ("Название", "Статус"):
                cell.number_format = money
            if best and r["Название"] == best["Название"]:
                cell.fill, cell.font = best_fill, bold
            elif not r["Доступен"]:
                cell.font = grey
    ws.column_dimensions["A"].width = 42
    for c in range(2, len(cols) + 1):
        ws.column_dimensions[get_column_letter(c)].width = 18
    ws.column_dimensions[get_column_letter(len(cols))].width = 30
    ws.freeze_panes = "B2"
    ws.append([])
    ws.append(["Зелёным выделен самый выгодный вариант, серым — недоступные."])

    # Лист 3. Расчёт по шагам
    ws = wb.create_sheet("Расчёт по шагам")
    ws.column_dimensions["A"].width = 100
    ws.column_dimensions["B"].width = 20
    for r in ordered:
        ws.append([r["Название"] + ("" if r["Доступен"] else " — недоступен: " + "; ".join(r["Причины"]))])
        ws.cell(ws.max_row, 1).font = Font(bold=True, size=12)
        if best and r["Название"] == best["Название"]:
            ws.cell(ws.max_row, 1).fill = best_fill
        for s in r["Шаги"]:
            ws.append(["• " + s])
            ws.cell(ws.max_row, 1).alignment = wrap
        summary = [(CHART_NAMES[k], r[k]) for k in COMPONENTS if r[k]]
        summary += [("Налоговая нагрузка", r["Итого"]),
                    ("Прибыль до налогов", r["Прибыль до налогов"]),
                    ("Чистая прибыль после налогов", r["Чистая прибыль"])]
        if r["Форма"] == "ООО":
            summary.append(("Чистая прибыль собственнику (после НДФЛ с дивидендов)", r["Собственнику"]))
        for name, value in summary:
            ws.append([name, value])
            ws.cell(ws.max_row, 2).number_format = money
            if name.startswith(("Налоговая нагрузка", "Чистая прибыль")):
                ws.cell(ws.max_row, 1).font = bold
                ws.cell(ws.max_row, 2).font = bold
        ws.append([])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Интерфейс
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Калькулятор налогов", page_icon="🧮", layout="wide")
st.title("🧮 Какую систему налогообложения выбрать")
st.caption("ИП и ООО: УСН, ОСНО, АУСН. Правила 2026 года. Расчёт ориентировочный — "
           "итоговое решение принимает бухгалтер с учётом всех обстоятельств.")

with st.sidebar:
    st.header("Исходные данные")
    income = st.number_input("Выручка за год (с НДС), ₽", min_value=0, value=12_000_000, step=500_000,
                             help="Всё, что платят клиенты за год, включая НДС, если он есть.")
    expenses = st.number_input("Расходы за год, ₽", min_value=0, value=5_000_000, step=500_000,
                               help="Без зарплаты и взносов — их укажите ниже.")
    vat_share = st.slider("Доля расходов с входящим НДС 22%, %", 0, 100, 50,
                          help="Какая часть расходов — от поставщиков с НДС. Влияет на вычеты на ОСНО и УСН с НДС 22%.")
    staff = st.number_input("Количество сотрудников", min_value=0, value=2, step=1)
    payroll = st.number_input("Фонд оплаты труда за год, ₽", min_value=0, value=1_200_000, step=100_000,
                              help="Начисленная зарплата до вычета НДФЛ, без взносов.")
    clients_vat = st.checkbox("Клиентам важен входящий НДС", value=False,
                              help="Клиенты — компании на ОСНО, которым нужен вычет НДС.")
    vat_discount = 0
    if clients_vat:
        vat_discount = st.slider("Скидка, которую потребуют клиенты без НДС, %", 0, 25, 15,
                                 help="Насколько придётся снизить цену, если вы не выставляете НДС 22%.")

    st.subheader("Ставка УСН")
    rate_kind = st.radio("Ставка УСН", ["Обычная: 6% и 15%", "Льготная ставка региона"],
                         label_visibility="collapsed",
                         help="Регион своим законом может снизить ставку УСН. Например, в Санкт-Петербурге.")
    usn_rates = {"ИП": (6.0, "", 15.0, ""), "ООО": (6.0, "", 15.0, "")}
    if rate_kind == "Льготная ставка региона":
        lg_d = st.number_input("Льготная ставка УСН «Доходы», %", min_value=0.0, max_value=6.0,
                               value=None, step=0.5, placeholder="введите ставку региона")
        lg_dr = st.number_input("Льготная ставка УСН «Доходы минус расходы», %", min_value=0.0,
                                max_value=15.0, value=None, step=0.5, placeholder="введите ставку региона")
        lg_ooo = st.checkbox("Льгота действует и для ООО", value=True,
                             help="Снимите галочку, если по закону региона льгота только для ИП.")
        if lg_d is None and lg_dr is None:
            st.caption("Введите ставку хотя бы по одному объекту. Пока поле пустое, считается обычная ставка.")
        lg = (6.0 if lg_d is None else lg_d, "" if lg_d is None else " (льгота)",
              15.0 if lg_dr is None else lg_dr, "" if lg_dr is None else " (льгота)")
        usn_rates["ИП"] = lg
        if lg_ooo:
            usn_rates["ООО"] = lg

    with st.expander("Тарифы взносов"):
        msp = st.checkbox("Льготный тариф взносов МСП (15% свыше 1,5 МРОТ)", value=False,
                          help="С 2026 года льгота сохранена не для всех отраслей — уточните по своему ОКВЭД.")

input_vat = expenses * vat_share / 100 * VAT / (1 + VAT)
p = dict(income=income, expenses=expenses, payroll=payroll, staff=int(staff), clients_vat=clients_vat,
         vat_discount=vat_discount, usn_rates=usn_rates, msp=msp, input_vat=input_vat)

if income <= 0:
    st.info("Введите выручку за год в панели слева.")
    st.stop()

rows = calc_all(p)
available = [r for r in rows if r["Доступен"]]
best = min(available, key=lambda r: r["Итого"]) if available else None

if best:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Самый выгодный режим", best["Название"])
    c2.metric("Прибыль до налогов", rub(best["Прибыль до налогов"]),
              help="Выручка с НДС минус расходы и зарплата сотрудников. Одинакова для всех вариантов.")
    c3.metric("Налоговая нагрузка", rub(best["Итого"]), f"{best['% от дохода']:.1f}% от выручки", delta_color="off")
    c4.metric("Чистая прибыль после налогов", rub(best["Собственнику"]),
              help="Прибыль до налогов минус налоговая нагрузка. У ООО — после НДФЛ с дивидендов.")

tab_cmp, tab_detail, tab_warn, tab_about = st.tabs(["📊 Сравнение", "🔍 Подробный расчёт", "⚠️ Предупреждения", "ℹ️ Допущения"])

with tab_cmp:
    df = pd.DataFrame(rows).sort_values(["Доступен", "Итого"], ascending=[False, True])
    df["Статус"] = df.apply(lambda r: "✅ доступен" if r["Доступен"] else "⛔ " + "; ".join(r["Причины"]), axis=1)
    cols = ["Название"] + COMPONENTS + ["Итого", "% от дохода", "Чистая прибыль", "Собственнику", "Статус"]
    show = df[cols].rename(columns={"Название": "Режим"}).reset_index(drop=True)
    best_name = best["Название"] if best else None

    st.markdown("**Как читать график.** Каждая полоса — один вариант: кто платит (ИП или ООО) и по какой системе. "
                "Длина полосы — сколько налогов и взносов придётся заплатить за год. Цветные части полосы "
                "показывают, из чего сложилась сумма: налог, НДС, взносы, НДФЛ с дивидендов. Справа от полосы — "
                "общая сумма в рублях и её доля от выручки. Сверху самый выгодный вариант (★), внизу самый дорогой. "
                "Бледные полосы со значком ⛔ — варианты, которые вам недоступны. "
                "Наведите мышь на часть полосы, чтобы увидеть точную сумму.")

    chart = show  # уже отсортировано: доступные по возрастанию нагрузки, недоступные в конце
    labels = [("★ " if n == best_name else ("⛔ " if s.startswith("⛔") else "")) + n
              for n, s in zip(chart["Режим"], chart["Статус"])]
    opacity = [0.35 if s.startswith("⛔") else 1.0 for s in chart["Статус"]]
    fig = go.Figure()
    for comp in COMPONENTS:
        if not chart[comp].any():
            continue
        fig.add_trace(go.Bar(
            name=CHART_NAMES[comp], x=chart[comp] / 1_000_000, y=labels, orientation="h",
            marker=dict(color=CHART_COLORS[comp], opacity=opacity), customdata=chart[comp],
            hovertemplate="%{y}<br>" + CHART_NAMES[comp] + ": %{customdata:,.0f} ₽<extra></extra>"))
    for y, v, pct in zip(labels, chart["Итого"], chart["% от дохода"]):
        fig.add_annotation(x=v / 1_000_000, y=y, text=f"  {rub(v)} · {pct:.1f}%".replace(".", ","),
                           showarrow=False, xanchor="left", font=dict(size=12))
    fig.update_layout(title="Налоговая нагрузка за год: из чего складывается сумма",
                      barmode="stack", yaxis=dict(autorange="reversed"),
                      xaxis=dict(title="млн ₽", tickformat=",.1f", range=[0, chart["Итого"].max() / 1_000_000 * 1.3]),
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                      height=40 * len(chart) + 180,
                      margin=dict(l=10, r=10, t=90, b=10), separators=", ")
    st.plotly_chart(fig, width="stretch")

    st.markdown("**Из чего складывается сумма.** По каждому варианту: сколько уходит на сам налог, НДС, взносы "
                "и НДФЛ с дивидендов. Сумма этих граф — налоговая нагрузка. Прибыль до налогов — выручка с НДС "
                "минус расходы и зарплата сотрудников. Чистая прибыль после налогов — прибыль до налогов минус "
                "налоги и взносы бизнеса. У ООО собственник получает деньги дивидендами, поэтому отдельно "
                "показано, сколько останется ему после НДФЛ с дивидендов.")
    names_map = {
        "Режим": "Вариант",
        "Налог": "Налог по системе (УСН / НДФЛ / на прибыль)",
        "НДС": "НДС к уплате",
        "Взносы ИП за себя": "Взносы ИП за себя",
        "Взносы за сотрудников": "Взносы за сотрудников",
        "НДФЛ с дивидендов": "НДФЛ с дивидендов собственника ООО",
        "Потери без НДС": "Скидка клиентам за работу без НДС",
        "Итого": "Налоговая нагрузка, ₽",
        "% от дохода": "Налоговая нагрузка, % от выручки",
        "Чистая прибыль": "Чистая прибыль после налогов, ₽",
        "Собственнику": "Чистая прибыль собственнику ООО (после НДФЛ с дивидендов), ₽",
        "Статус": "Доступен ли",
    }
    money_cols = COMPONENTS + ["Итого", "Чистая прибыль", "Собственнику"]
    table = show.copy()
    for c in money_cols:
        table[c] = table[c].map(lambda v: f"{v:,.0f}".replace(",", " "))
    table.loc[~table["Режим"].str.startswith("ООО"), "Собственнику"] = "—"
    table["% от дохода"] = table["% от дохода"].map(lambda v: f"{v:.1f}%".replace(".", ","))
    if not table["Потери без НДС"].ne("0").any():
        table = table.drop(columns=["Потери без НДС"])
    table = table.rename(columns=names_map)

    def highlight(row):
        if row["Вариант"] == best_name:
            return ["background-color: #c8f0d0; color: #0b3d1a; font-weight: 600"] * len(row)
        if row["Доступен ли"].startswith("⛔"):
            return ["color: #999999"] * len(row)
        return [""] * len(row)

    st.dataframe(table.style.apply(highlight, axis=1), hide_index=True, width="stretch",
                 height=38 * (len(table) + 1))

    try:
        xlsx = build_xlsx(p, rows, best)
    except ImportError:
        st.warning("Для скачивания в Excel поставьте библиотеку: закройте калькулятор (Ctrl+C в чёрном окне), "
                   "выполните `pip install openpyxl` и снова `streamlit run app.py`.")
    else:
        st.download_button("📥 Скачать расчёт в Excel", xlsx, file_name="Расчёт налогов.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           type="primary")
        st.caption("В файле три листа: исходные данные, сравнение всех вариантов и расчёт по шагам для каждого.")

with tab_detail:
    names = [r["Название"] for r in rows]
    default = names.index(best["Название"]) if best else 0
    pick = st.selectbox("Режим", names, index=default)
    r = next(x for x in rows if x["Название"] == pick)
    if not r["Доступен"]:
        st.error("Режим недоступен: " + "; ".join(r["Причины"]))
    for s in r["Шаги"]:
        st.markdown(f"- {s}")
    st.markdown(f"**Налоговая нагрузка: {rub(r['Итого'])} ({r['% от дохода']:.1f}% от выручки). "
                f"Прибыль до налогов: {rub(r['Прибыль до налогов'])}. "
                f"Чистая прибыль после налогов: {rub(r['Чистая прибыль'])}.**")
    if r["Форма"] == "ООО":
        st.markdown(f"**Чистая прибыль собственнику ООО (после НДФЛ с дивидендов): {rub(r['Собственнику'])}.**")
    parts = {k: r[k] for k in COMPONENTS if r[k]}
    if parts:
        st.bar_chart(pd.Series(parts, name="₽"), horizontal=True)

with tab_warn:
    for level, text in warnings(p, best):
        getattr(st, level)(text)

with tab_about:
    st.markdown(f"""
**Что заложено в расчёт (2026 год)**
- Взносы ИП за себя: {rub(IP_FIXED)} + 1% с дохода свыше {rub(IP_1PCT_THRESHOLD)}, но не больше {rub(IP_1PCT_CAP)}.
  На УСН «Доходы» 1% считается с дохода, на УСН «Д−Р» и ОСНО — с дохода за вычетом расходов.
- УСН «Доходы»: ИП без сотрудников уменьшает налог на свои взносы полностью, с сотрудниками и ООО — не более чем на 50%.
- УСН «Д−Р»: минимальный налог 1% от дохода.
- НДС на УСН: освобождение до {rub(USN_VAT_FREE)}, далее 5% до {rub(USN_VAT5_LIMIT)}, 7% до {rub(USN_LIMIT)} (без вычетов) или 22% с вычетами.
- НДФЛ ИП на ОСНО: 13% / 15% / 18% / 20% / 22% с порогами 2,4 / 5 / 20 / 50 млн ₽.
- ООО: налог на прибыль 25%, вся чистая прибыль выплачивается дивидендами, НДФЛ 13% до 2,4 млн ₽ и 15% сверх.
- Взносы за сотрудников: 30% до {rub(INS_BASE)} на человека и 15,1% сверх, плюс 0,2% «на травматизм».
- АУСН: 8% или 20% (минимум 3% от дохода), без взносов за сотрудников и за себя.
- НДФЛ сотрудников удерживается из их зарплаты и в нагрузку бизнеса не входит.

**Почему цифры могут не совпасть с ручным расчётом в Excel**
- ИП на ОСНО: НДФЛ считается по прогрессивной шкале 13 / 15 / 18 / 20 / 22%, а не ровно 18% со всей прибыли.
- ООО: в нагрузку входит НДФЛ с дивидендов — собственник забирает прибыль только после этого налога.
  Поэтому показаны обе цифры: чистая прибыль компании и чистая прибыль собственнику.
- Взносы ИП за себя: {rub(IP_FIXED)} + 1% с суммы свыше {rub(IP_1PCT_THRESHOLD)}. {rub(IP_1PCT_CAP)} — это потолок
  только для 1%, а не сумма взносов.
- Уменьшение налога на взносы не бывает отрицательным: налог уменьшается на взносы, но не больше лимита (100% или 50%).

**Чего калькулятор не учитывает:** зарплату собственника ООО вместо дивидендов, патент, торговый сбор,
имущественные налоги, региональные льготы кроме ставки УСН, освобождение от НДС по ст. 145 НК РФ на ОСНО.
""")
