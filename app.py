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

# ПСН: лимит доходов по году (подп. 1 п. 6 ст. 346.45 НК в ред. 425-ФЗ), с 2028 года — 10 млн ₽.
# При совмещении с УСН складывается выручка по обоим режимам.
PSN_LIMITS = {2026: 20_000_000, 2027: 15_000_000, 2028: 10_000_000}
PSN_YEARS = tuple(PSN_LIMITS)
PSN_STAFF = 15
PSN_RATE = 0.06
MONTHS_IN = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе",
             "сентябре", "октябре", "ноябре", "декабре"]

COMPONENTS = ["Налог", "Патент", "НДС", "Взносы ИП за себя", "Взносы за сотрудников",
              "НДФЛ с дивидендов", "Потери без НДС"]
OPTIONAL = ("Патент", "Потери без НДС")  # графы, которые прячутся, если везде ноль


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


def pct(rate: float) -> str:
    """0.075 → «7,5%»."""
    return f"{rate * 100:g}%".replace(".", ",")


def staff_contrib(payroll: float, staff: int, msp: bool):
    """Взносы с ФОТ по средней зарплате на сотрудника: (сумма, как посчитано)."""
    if payroll <= 0 or staff <= 0:
        return 0.0, ""
    per = payroll / staff
    each = f" на каждого из {staff} сотрудников" if staff > 1 else ""
    if msp:
        threshold = 1.5 * MROT * 12
        main = min(per, threshold) * 0.30 + max(per - threshold, 0) * 0.15
        how = (f"зарплата {rub(per)}{each}: до {rub(threshold)} (1,5 МРОТ × 12) × 30%"
               + (f", сверх — {rub(per - threshold)} × 15%" if per > threshold else ""))
    else:
        main = min(per, INS_BASE) * 0.30 + max(per - INS_BASE, 0) * 0.151
        how = (f"зарплата {rub(per)}{each} × 30%"
               + (f" (до {rub(INS_BASE)}), сверх — {rub(per - INS_BASE)} × 15,1%" if per > INS_BASE else ""))
    how += f" + {rub(per)} × 0,2% «на травматизм»"
    if staff > 1:
        how += f"; × {staff} чел."
    return (main + per * INJURY) * staff, how


def progressive_how(amount: float, scale) -> str:
    """Как считается налог по шкале: «2 400 000 ₽ × 13% + (X − 2 400 000 ₽) × 15%»."""
    parts, lower = [], 0.0
    for upper, rate in scale:
        if amount <= lower:
            break
        if lower == 0:
            parts.append(f"{rub(min(amount, upper))} × {pct(rate)}")
        else:
            parts.append(f"({rub(min(amount, upper))} − {rub(lower)}) × {pct(rate)}")
        lower = upper
    return " + ".join(parts) if parts else "прибыли нет — налога нет"


def own_contrib_steps(steps, base_1pct: float, base_how: str) -> float:
    """Взносы ИП за себя с расшифровкой. Возвращает сумму."""
    extra = min(max(base_1pct - IP_1PCT_THRESHOLD, 0) * 0.01, IP_1PCT_CAP)
    steps.append(("Взносы ИП за себя: фиксированные", "сумма на 2026 год, одинакова для всех ИП", IP_FIXED))
    steps.append(("Взносы ИП за себя: 1%",
                  f"({base_how} − {rub(IP_1PCT_THRESHOLD)}) × 1%, но не больше {rub(IP_1PCT_CAP)}", extra))
    steps.append(("Взносы ИП за себя, всего", f"{rub(IP_FIXED)} + {rub(extra)}", IP_FIXED + extra))
    return IP_FIXED + extra


def dividend_steps(steps, profit: float, profit_how: str) -> float:
    """Чистая прибыль ООО к выплате и НДФЛ с дивидендов. Возвращает НДФЛ."""
    profit = max(profit, 0)
    steps.append(("Чистая прибыль к выплате дивидендами", profit_how, profit))
    ndfl = progressive(profit, DIV_SCALE)
    steps.append(("НДФЛ с дивидендов", "шкала для дивидендов: 13% с первых 2 400 000 ₽, 15% со всего, что больше. "
                  "Расчёт: " + progressive_how(profit, DIV_SCALE), ndfl))
    return ndfl


def input_steps(steps, p):
    steps.append(("Выручка с НДС", "поле «Выручка за год (с НДС)»", p["income"]))
    steps.append(("Расходы", "поле «Расходы за год»", p["expenses"]))
    if p["payroll"]:
        steps.append(("Зарплата сотрудников", "поле «Фонд оплаты труда за год»", p["payroll"]))


def vat22_steps(steps, p):
    """НДС 22% с вычетом: (НДС к уплате, выручка без НДС, расходы без НДС)."""
    income, expenses = p["income"], p["expenses"]
    out_vat = income * VAT / (1 + VAT)
    steps.append(("НДС начисленный 22%", f"{rub(income)} × 22 ÷ 122 (НДС, который сидит в выручке)", out_vat))
    steps.append(("НДС входящий (вычет)",
                  "поле «в т.ч. входящий НДС к вычету» — НДС из счетов поставщиков, он уже входит в расходы",
                  p["input_vat"]))
    vat = out_vat - p["input_vat"]
    steps.append(("НДС к уплате", f"{rub(out_vat)} − {rub(p['input_vat'])}", vat))
    inc, exp = income - out_vat, expenses - p["input_vat"]
    steps.append(("Выручка без НДС", f"{rub(income)} − {rub(out_vat)}", inc))
    steps.append(("Расходы без НДС", f"{rub(expenses)} − {rub(p['input_vat'])}", exp))
    return vat, inc, exp


def staff_steps(steps, p) -> float:
    sc, how = staff_contrib(p["payroll"], p["staff"], p["msp"])
    if sc:
        steps.append(("Взносы за сотрудников", how, sc))
    return sc


# ---------------------------------------------------------------------------
# Расчёт режимов. Шаг расчёта — (что считаем, откуда берём и как считаем, сумма)
# ---------------------------------------------------------------------------
def result(form, regime, comps, steps, problems, p):
    total = sum(comps.values())
    pbt = p["income"] - p["expenses"] - p["payroll"]
    names = {"Налог": "налог", "Патент": "патент", "НДС": "НДС", "Взносы ИП за себя": "взносы ИП",
             "Взносы за сотрудников": "взносы за сотрудников", "НДФЛ с дивидендов": "НДФЛ с дивидендов",
             "Потери без НДС": "скидка клиентам"}
    used = [f"{names[k]} {rub(v)}" for k, v in comps.items() if v]
    steps = steps + [
        ("Налоговая нагрузка", " + ".join(used) if used else "налогов нет", total),
        ("Налоговая нагрузка, % от выручки", f"{rub(total)} ÷ {rub(p['income'])} × 100",
         f"{total / p['income'] * 100:.1f}%".replace(".", ",") if p["income"] else "—"),
        ("Прибыль до налогов", f"выручка {rub(p['income'])} − расходы {rub(p['expenses'])}"
                               + (f" − зарплата {rub(p['payroll'])}" if p["payroll"] else ""), pbt),
        ("Чистая прибыль после налогов",
         f"{rub(pbt)} − налоговая нагрузка без НДФЛ с дивидендов {rub(total - comps['НДФЛ с дивидендов'])}",
         pbt - total + comps["НДФЛ с дивидендов"]),
    ]
    if form == "ООО":
        steps.append(("Чистая прибыль собственнику ООО",
                      f"{rub(pbt - total + comps['НДФЛ с дивидендов'])} − НДФЛ с дивидендов "
                      f"{rub(comps['НДФЛ с дивидендов'])}", pbt - total))
    return {
        "Форма": form,
        "Режим": regime,
        "Название": f"{form} · {regime}",
        **comps,
        "Итого": total,
        "% от дохода": total / p["income"] * 100 if p["income"] else 0.0,
        "Прибыль до налогов": pbt,
        "Чистая прибыль": pbt - total + comps["НДФЛ с дивидендов"],
        "Собственнику": pbt - total,
        "Доступен": not problems,
        "Причины": problems,
        "Шаги": steps,
    }


def vat_loss(p, vat_rate: float) -> float:
    """Скидка, которую потребуют клиенты-плательщики НДС за отсутствие вычета."""
    if not p["clients_vat"]:
        return 0.0
    return p["income"] * p["vat_discount"] / 100 * (1 - vat_rate / VAT)


def vat_loss_step(steps, p, vat_rate, comps):
    comps["Потери без НДС"] = vat_loss(p, vat_rate)
    if comps["Потери без НДС"]:
        steps.append(("Скидка клиентам за работу без полного НДС",
                      f"{rub(p['income'])} × {p['vat_discount']}% (поле «Скидка…») × (1 − {pct(vat_rate)} ÷ 22%)",
                      comps["Потери без НДС"]))


def calc_usn(p, form, obj, vat_mode):
    """obj: 'Д' или 'ДР'; vat_mode: None (освобождение), 'reduced' (5/7%), 'full' (22%)."""
    income, payroll, staff = p["income"], p["payroll"], p["staff"]
    comps = dict.fromkeys(COMPONENTS, 0.0)
    steps, problems = [], []
    input_steps(steps, p)

    if vat_mode is None:
        inc, exp, vat_rate = income, p["expenses"], 0.0
        steps.append(("НДС", f"не платится: выручка не больше {rub(USN_VAT_FREE)} — освобождение", 0.0))
        if income > USN_VAT_FREE:
            problems.append(f"доход выше {rub(USN_VAT_FREE)} — освобождение от НДС не действует")
        title_vat = ""
    elif vat_mode == "reduced":
        vat_rate = 0.05 if income <= USN_VAT5_LIMIT else 0.07
        k = round(vat_rate * 100)
        comps["НДС"] = income * vat_rate / (1 + vat_rate)
        steps.append((f"НДС {k}% к уплате", f"{rub(income)} × {k} ÷ {100 + k} (вычетов на этой ставке нет)",
                      comps["НДС"]))
        inc, exp = income - comps["НДС"], p["expenses"]
        steps.append(("Выручка без НДС", f"{rub(income)} − {rub(comps['НДС'])}", inc))
        title_vat = f" + НДС {k}%"
    else:
        vat_rate = VAT
        comps["НДС"], inc, exp = vat22_steps(steps, p)
        title_vat = " + НДС 22%"

    if income > USN_LIMIT:
        problems.append(f"доход выше лимита УСН {rub(USN_LIMIT)}")
    if staff > USN_STAFF:
        problems.append(f"сотрудников больше {USN_STAFF}")

    sc = staff_steps(steps, p)
    comps["Взносы за сотрудников"] = sc

    rate_d, mark_d, rate_dr, mark_dr = p["usn_rates"][form]

    if obj == "Д":
        rate = rate_d / 100
        src = "льготная ставка региона" if mark_d else "обычная ставка"
        base_tax = inc * rate
        steps.append(("Налог УСН до уменьшения", f"выручка без НДС {rub(inc)} × {pct(rate)} ({src})", base_tax))
        if form == "ИП":
            own = own_contrib_steps(steps, inc, rub(inc))
            comps["Взносы ИП за себя"] = own
            if staff > 0:
                reduce = min(own + sc, base_tax * 0.5)
                how = (f"взносы {rub(own)} + {rub(sc)} = {rub(own + sc)}; есть сотрудники, поэтому не больше "
                       f"50% налога: {rub(base_tax)} × 50% = {rub(base_tax * 0.5)}; берём меньшее")
            else:
                reduce = min(own, base_tax)
                how = f"взносы ИП {rub(own)}; сотрудников нет — можно уменьшить до нуля, но не больше налога {rub(base_tax)}"
        else:
            reduce = min(sc, base_tax * 0.5)
            how = (f"взносы за сотрудников {rub(sc)}, но не больше 50% налога: "
                   f"{rub(base_tax)} × 50% = {rub(base_tax * 0.5)}; берём меньшее")
        if reduce:
            steps.append(("Уменьшение налога на взносы", how, -reduce))
        comps["Налог"] = base_tax - reduce
        steps.append(("Налог УСН к уплате", f"{rub(base_tax)} − {rub(reduce)}", comps["Налог"]))
        regime = f"УСН «Доходы» {rate_d:g}%{mark_d}{title_vat}"
    else:
        rate = rate_dr / 100
        src = "льготная ставка региона" if mark_dr else "обычная ставка"
        own = 0.0
        if form == "ИП":
            diff = inc - exp - payroll - sc
            steps.append(("Разница для взноса 1%",
                          f"{rub(inc)} − {rub(exp)}" + (f" − {rub(payroll)} − {rub(sc)}" if payroll else ""), diff))
            own = own_contrib_steps(steps, diff, rub(diff))
            comps["Взносы ИП за себя"] = own
        base = inc - exp - payroll - sc - own
        how = f"выручка без НДС {rub(inc)} − расходы {rub(exp)}"
        if payroll:
            how += f" − зарплата {rub(payroll)} − взносы за сотрудников {rub(sc)}"
        if own:
            how += f" − взносы ИП {rub(own)}"
        steps.append(("Налоговая база", how, base))
        tax = max(base, 0) * rate
        steps.append(("Налог по ставке", f"{rub(max(base, 0))} × {pct(rate)} ({src})", tax))
        min_tax = inc * 0.01
        steps.append(("Минимальный налог", f"{rub(inc)} × 1%", min_tax))
        comps["Налог"] = max(tax, min_tax)
        steps.append(("Налог УСН к уплате", "больший из двух: налог по ставке или минимальный", comps["Налог"]))
        regime = f"УСН «Д−Р» {rate_dr:g}%{mark_dr}{title_vat}"

    if form == "ООО":
        profit = inc - exp - payroll - sc - comps["Налог"]
        how = f"{rub(inc)} − {rub(exp)}" + (f" − {rub(payroll)} − {rub(sc)}" if payroll else "") + f" − налог {rub(comps['Налог'])}"
        comps["НДФЛ с дивидендов"] = dividend_steps(steps, profit, how)

    vat_loss_step(steps, p, vat_rate, comps)
    return result(form, regime, comps, steps, problems, p)


def calc_osno(p, form):
    payroll = p["payroll"]
    comps = dict.fromkeys(COMPONENTS, 0.0)
    steps = []
    input_steps(steps, p)
    comps["НДС"], inc, exp = vat22_steps(steps, p)
    sc = staff_steps(steps, p)
    comps["Взносы за сотрудников"] = sc
    profit = inc - exp - payroll - sc
    profit_how = f"{rub(inc)} − {rub(exp)}" + (f" − {rub(payroll)} − {rub(sc)}" if payroll else "")
    if form == "ИП":
        steps.append(("Прибыль (для взноса 1% и НДФЛ)", profit_how, profit))
        own = own_contrib_steps(steps, profit, rub(profit))
        comps["Взносы ИП за себя"] = own
        base = max(profit - own, 0)
        steps.append(("База НДФЛ", f"{rub(profit)} − взносы ИП {rub(own)}", base))
        comps["Налог"] = progressive(base, NDFL_SCALE)
        steps.append(("НДФЛ (прогрессивная шкала 13–22%)",
                      "шкала НДФЛ для ИП: 13% до 2 400 000 ₽, 15% от 2 400 000 до 5 000 000 ₽, "
                      "18% от 5 до 20 млн ₽, 20% от 20 до 50 млн ₽, 22% свыше 50 млн ₽. "
                      "Расчёт: " + progressive_how(base, NDFL_SCALE), comps["Налог"]))
    else:
        steps.append(("Прибыль", profit_how, profit))
        comps["Налог"] = max(profit, 0) * 0.25
        steps.append(("Налог на прибыль", f"{rub(max(profit, 0))} × 25%", comps["Налог"]))
        comps["НДФЛ с дивидендов"] = dividend_steps(
            steps, profit - comps["Налог"], f"{rub(profit)} − налог на прибыль {rub(comps['Налог'])}")
    vat_loss_step(steps, p, VAT, comps)
    return result(form, "ОСНО", comps, steps, [], p)


def calc_ausn(p, form, obj):
    income, expenses, payroll, staff = p["income"], p["expenses"], p["payroll"], p["staff"]
    comps = dict.fromkeys(COMPONENTS, 0.0)
    problems = []
    if income > AUSN_LIMIT:
        problems.append(f"доход выше {rub(AUSN_LIMIT)}")
    if staff > AUSN_STAFF:
        problems.append(f"сотрудников больше {AUSN_STAFF}")
    steps = []
    input_steps(steps, p)
    steps.append(("НДС, взносы ИП и взносы за сотрудников", "на АУСН не платятся", 0.0))
    if obj == "Д":
        comps["Налог"] = income * 0.08
        steps.append(("Налог АУСН к уплате", f"выручка {rub(income)} × 8%", comps["Налог"]))
        regime = "АУСН «Доходы» 8%"
    else:
        base = income - expenses - payroll
        steps.append(("Налоговая база", f"выручка {rub(income)} − расходы {rub(expenses)}"
                                        + (f" − зарплата {rub(payroll)}" if payroll else ""), base))
        tax = max(base, 0) * 0.20
        steps.append(("Налог по ставке", f"{rub(max(base, 0))} × 20%", tax))
        min_tax = income * AUSN_MIN
        steps.append(("Минимальный налог", f"{rub(income)} × 3%", min_tax))
        comps["Налог"] = max(tax, min_tax)
        steps.append(("Налог АУСН к уплате", "больший из двух: налог по ставке или минимальный", comps["Налог"]))
        regime = "АУСН «Д−Р» 20%"
    if form == "ООО":
        profit = income - expenses - payroll - comps["Налог"]
        how = f"{rub(income)} − {rub(expenses)}" + (f" − {rub(payroll)}" if payroll else "") + f" − налог {rub(comps['Налог'])}"
        comps["НДФЛ с дивидендов"] = dividend_steps(steps, profit, how)
    vat_loss_step(steps, p, 0.0, comps)
    return result(form, regime, comps, steps, problems, p)


def psn_limit(year: int) -> int:
    return PSN_LIMITS[min(year, max(PSN_LIMITS))]


def psn_check(year: int, prev: float, cur: float, staff_psn: int):
    """Право на патент в году: (причины, месяц превышения лимита или None)."""
    limit = psn_limit(year)
    problems, month = [], None
    if prev > limit:
        problems.append(f"доход за {year - 1} год {rub(prev)} больше лимита ПСН на {year} год {rub(limit)} — "
                        "патент не выдадут")
    elif cur > limit:
        # выручка идёт равномерно по месяцам: ищем месяц, в котором итог с начала года превысит лимит
        month = int(limit * 12 // cur) + 1
        problems.append(f"доход {year} года {rub(cur)} больше лимита {rub(limit)}: превышение в {MONTHS_IN[month - 1]}, "
                        "право на патент теряется с начала его срока")
    if staff_psn > PSN_STAFF:
        problems.append(f"на патенте больше {PSN_STAFF} сотрудников")
    return problems, month


def psn_objects(p):
    """Если вся выручка на патенте, объект УСН не важен — один вариант."""
    return ("Д",) if p["psn"]["share"] >= 100 else ("Д", "ДР")


def calc_psn(p, obj, year=PSN_YEARS[0], prev=None):
    """ИП: патент на часть выручки, остальное — УСН (obj 'Д' или 'ДР')."""
    s = p["psn"]
    income, payroll, staff, pvd = p["income"], p["payroll"], p["staff"], s["pvd"]
    k = s["share"] / 100
    psn_inc, usn_inc = income * k, income * (1 - k)
    psn_staff = min(s["staff"], staff)
    usn_staff = staff - psn_staff
    comps = dict.fromkeys(COMPONENTS, 0.0)
    steps = []
    input_steps(steps, p)
    steps.append(("Выручка по патенту", f"{rub(income)} × {s['share']}% (поле «Доля выручки на патенте»)", psn_inc))
    if usn_inc:
        steps.append(("Выручка по УСН", f"{rub(income)} − {rub(psn_inc)}", usn_inc))
    problems, _ = psn_check(year, s["prev_income"] if prev is None else prev, income, psn_staff)

    # по патенту НДС нет; освобождение на УСН — по выручке обоих режимов
    vat_rate, title_vat = 0.0, ""
    if usn_inc and income > USN_VAT_FREE:
        vat_rate = 0.05 if income <= USN_VAT5_LIMIT else 0.07
        r = round(vat_rate * 100)
        comps["НДС"] = usn_inc * vat_rate / (1 + vat_rate)
        steps.append((f"НДС {r}% к уплате", f"выручка по УСН {rub(usn_inc)} × {r} ÷ {100 + r}: выручка обоих режимов "
                                            f"больше {rub(USN_VAT_FREE)}; по патенту НДС нет", comps["НДС"]))
        title_vat = f" + НДС {r}%"
    else:
        steps.append(("НДС", "не платится: по патенту НДС нет" + (
            f", на УСН — освобождение: выручка обоих режимов не больше {rub(USN_VAT_FREE)}" if usn_inc else ""), 0.0))
    usn_net = usn_inc - comps["НДС"]
    if usn_inc and income > USN_LIMIT:
        problems.append(f"доход выше лимита УСН {rub(USN_LIMIT)}")
    if usn_inc and staff > USN_STAFF:
        problems.append(f"сотрудников больше {USN_STAFF}")

    sc = staff_steps(steps, p)
    comps["Взносы за сотрудников"] = sc
    sc_psn = sc * psn_staff / staff if staff else 0.0
    sc_usn = sc - sc_psn
    if sc_psn and sc_usn:
        steps.append(("Из них взносы за сотрудников на патенте", f"{rub(sc)} × {psn_staff} из {staff} чел.", sc_psn))
    exp_usn = p["expenses"] * (1 - k)
    pay_usn = payroll * usn_staff / staff if staff else 0.0

    # взносы ИП: 1% с потенциального дохода по патенту плюс доход по УСН
    if not usn_inc:
        usn_base, usn_how = 0.0, ""
    elif obj == "Д":
        usn_base, usn_how = usn_net, f"доход по УСН {rub(usn_net)}"
    else:
        usn_base = max(usn_net - exp_usn - pay_usn - sc_usn, 0)
        usn_how = f"доходы минус расходы по УСН {rub(usn_base)}"
    base_1pct = usn_base + pvd
    steps.append(("База для взноса 1%", (usn_how + " + " if usn_how else "")
                  + f"потенциальный доход по патенту {rub(pvd)}", base_1pct))
    own = own_contrib_steps(steps, base_1pct, rub(base_1pct))
    comps["Взносы ИП за себя"] = own
    own_psn = own * k
    own_usn = own - own_psn
    if usn_inc:
        steps.append(("Взносы ИП за себя: доля патента",
                      f"{rub(own)} × {s['share']}% — делятся между режимами пропорционально выручке", own_psn))

    psn_tax = pvd * PSN_RATE
    steps.append(("Налог по патенту до уменьшения", f"потенциальный доход {rub(pvd)} × 6%", psn_tax))
    if psn_staff:
        red = min(own_psn + sc_psn, psn_tax * 0.5)
        how = (f"взносы {rub(own_psn)} + {rub(sc_psn)} = {rub(own_psn + sc_psn)}; на патенте есть сотрудники, "
               f"поэтому не больше 50% налога: {rub(psn_tax * 0.5)}; берём меньшее")
    else:
        red = min(own_psn, psn_tax)
        how = (f"взносы ИП {rub(own_psn)}; сотрудников на патенте нет — можно до нуля, "
               f"но не больше налога {rub(psn_tax)}")
    if red:
        steps.append(("Уменьшение патента на взносы", how, -red))
    comps["Патент"] = psn_tax - red
    steps.append(("Налог по патенту к уплате", f"{rub(psn_tax)} − {rub(red)}", comps["Патент"]))

    rate_d, mark_d, rate_dr, mark_dr = p["usn_rates"]["ИП"]
    if not usn_inc:
        regime = "ПСН (вся выручка на патенте)"
    elif obj == "Д":
        rate = rate_d / 100
        src = "льготная ставка региона" if mark_d else "обычная ставка"
        base_tax = usn_net * rate
        steps.append(("Налог УСН до уменьшения", f"выручка по УСН без НДС {rub(usn_net)} × {pct(rate)} ({src})", base_tax))
        if usn_staff:
            reduce = min(own_usn + sc_usn, base_tax * 0.5)
            how = (f"взносы {rub(own_usn)} + {rub(sc_usn)} = {rub(own_usn + sc_usn)}; на УСН есть сотрудники, "
                   f"поэтому не больше 50% налога: {rub(base_tax * 0.5)}; берём меньшее")
        else:
            reduce = min(own_usn, base_tax)
            how = f"доля взносов ИП на УСН {rub(own_usn)}, но не больше налога {rub(base_tax)}"
        if reduce:
            steps.append(("Уменьшение налога на взносы", how, -reduce))
        comps["Налог"] = base_tax - reduce
        steps.append(("Налог УСН к уплате", f"{rub(base_tax)} − {rub(reduce)}", comps["Налог"]))
        regime = f"ПСН + УСН «Доходы» {rate_d:g}%{mark_d}{title_vat}"
    else:
        rate = rate_dr / 100
        src = "льготная ставка региона" if mark_dr else "обычная ставка"
        base = usn_net - exp_usn - pay_usn - sc_usn - own_usn
        how = (f"выручка по УСН без НДС {rub(usn_net)} − расходы {rub(exp_usn)} "
               f"({100 - s['share']}% расходов — по доле выручки)")
        if pay_usn:
            how += f" − зарплата {rub(pay_usn)} − взносы за сотрудников {rub(sc_usn)}"
        how += f" − доля взносов ИП {rub(own_usn)}"
        steps.append(("Налоговая база УСН", how, base))
        tax = max(base, 0) * rate
        steps.append(("Налог по ставке", f"{rub(max(base, 0))} × {pct(rate)} ({src})", tax))
        min_tax = usn_net * 0.01
        steps.append(("Минимальный налог", f"{rub(usn_net)} × 1%", min_tax))
        comps["Налог"] = max(tax, min_tax)
        steps.append(("Налог УСН к уплате", "больший из двух: налог по ставке или минимальный", comps["Налог"]))
        regime = f"ПСН + УСН «Д−Р» {rate_dr:g}%{mark_dr}{title_vat}"

    vat_loss_step(steps, p, vat_rate * (1 - k), comps)
    return result("ИП", regime, comps, steps, problems, p)


def psn_forecast(p):
    """Патент на 2026–2028 годы: право, месяц превышения лимита, нагрузка с патентом и без."""
    s = p["psn"]
    g = s["growth"] / 100
    psn_staff = min(s["staff"], p["staff"])
    out = []
    for i, year in enumerate(PSN_YEARS):
        f = (1 + g) ** i
        py = dict(p, income=p["income"] * f, expenses=p["expenses"] * f, payroll=p["payroll"] * f,
                  input_vat=p["input_vat"] * f)
        prev = s["prev_income"] if i == 0 else p["income"] * (1 + g) ** (i - 1)
        problems, month = psn_check(year, prev, py["income"], psn_staff)
        modes = (None,) if py["income"] <= USN_VAT_FREE else ("reduced", "full")
        usn = [calc_usn(py, "ИП", o, m) for m in modes for o in ("Д", "ДР")]
        usn = [r for r in usn if r["Доступен"]]
        psn = [calc_psn(py, o, year, prev) for o in psn_objects(p)]
        out.append(dict(year=year, limit=psn_limit(year), prev=prev, income=py["income"], problems=problems,
                        month=month, usn=min(usn, key=lambda r: r["Итого"]) if usn else None,
                        psn=min(psn, key=lambda r: r["Итого"])))
    return out


def psn_verdict(years) -> tuple[str, str]:
    """Вывод по годам: (уровень сообщения, текст)."""
    ok = [y["year"] for y in years if not y["problems"]]
    bad = next((y for y in years if y["problems"]), None)
    if not bad:
        return "success", (f"Патент можно применять во всех годах: {', '.join(map(str, ok))}. "
                           f"С 2028 года лимит {rub(psn_limit(2028))} — следите, чтобы выручка его не превышала.")
    text = f"Патент можно применять: {', '.join(map(str, ok))} г. " if ok else ""
    if bad["month"]:
        text += (f"С патента клиент слетает в {bad['year']} году: в {MONTHS_IN[bad['month'] - 1]} выручка с начала "
                 f"года превысит лимит {rub(bad['limit'])}. Право теряется с начала срока патента — налог за весь "
                 "срок пересчитают по УСН, а уплаченный патент зачтут в налог УСН.")
    else:
        text += f"В {bad['year']} году патент не получить: " + "; ".join(bad["problems"]) + "."
    later = [str(y["year"]) for y in years if y["year"] > bad["year"] and y["problems"]]
    if later:
        text += f" В {', '.join(later)} г. патент тоже недоступен."
    return "error" if bad["year"] == PSN_YEARS[0] else "warning", text


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
        if form == "ИП" and p["psn"]:
            rows += [calc_psn(p, o) for o in psn_objects(p)]
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
    if p["psn"]:
        s = p["psn"]
        if s["staff"] > p["staff"]:
            w.append(("warning", f"Сотрудников на патенте указано {s['staff']}, а всего {p['staff']} — "
                                 f"в расчёте взято {p['staff']}."))
        if not s["pvd"]:
            w.append(("warning", "Не указан потенциальный доход по патенту — налог по патенту посчитан нулевым."))
        w.append(("info", "Патент — только для ИП и только по видам деятельности из закона субъекта. "
                          "Опт, поставка, продажа через маркетплейсы на патент не переводятся. "
                          f"Лимит выручки по УСН и патенту вместе: {rub(PSN_LIMITS[2026])} в 2026 году, "
                          f"{rub(PSN_LIMITS[2027])} в 2027 году, {rub(PSN_LIMITS[2028])} с 2028 года — "
                          "и за прошлый год, и с начала текущего."))
    return w


# ---------------------------------------------------------------------------
# Названия и цвета частей налоговой нагрузки, выгрузка в Excel
# ---------------------------------------------------------------------------
CHART_NAMES = {
    "Налог": "Налог по системе",
    "Патент": "Налог по патенту",
    "НДС": "НДС к уплате",
    "Взносы ИП за себя": "Взносы ИП за себя",
    "Взносы за сотрудников": "Взносы за сотрудников",
    "НДФЛ с дивидендов": "НДФЛ с дивидендов",
    "Потери без НДС": "Скидка клиентам без НДС",
}
CHART_COLORS = {
    "Налог": "#FF8A00",
    "Патент": "#E0B300",
    "НДС": "#0A6CFF",
    "Взносы ИП за себя": "#9B6BC4",
    "Взносы за сотрудников": "#12A33A",
    "НДФЛ с дивидендов": "#D9534F",
    "Потери без НДС": "#9E9E9E",
}
XLSX_COLUMNS = [
    ("Название", "Вариант"),
    ("Налог", "Налог по системе (УСН / НДФЛ / на прибыль), ₽"),
    ("Патент", "Налог по патенту, ₽"),
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


PART_STEPS = ("к уплате", "Взносы ИП за себя, всего", "Взносы за сотрудников", "НДФЛ с дивидендов",
              "НДФЛ (прогрессивная", "Налог на прибыль", "Скидка клиентам")
TOTAL_STEPS = ("Налоговая нагрузка", "Чистая прибыль после", "Чистая прибыль собственнику")


def is_part_step(name: str) -> bool:
    """Шаг, сумма которого входит в налоговую нагрузку."""
    return any(k in name for k in PART_STEPS) and not name.startswith("НДС начисленный")


def is_total_step(name: str) -> bool:
    return name.startswith(TOTAL_STEPS)


def fmt_value(value) -> str:
    if isinstance(value, str):
        return value
    return f"{value:,.2f} ₽".replace(",", " ").replace(".", ",")


def steps_html(steps) -> str:
    """Таблица «что считаем — как считаем — сумма» для вкладки «Подробный расчёт»."""
    from html import escape
    rows = []
    for name, how, value in steps:
        style = ""
        if is_part_step(name):
            style = "background:#E6F8CF;"
        if is_total_step(name):
            style += "font-weight:700;background:#C9F29B;"
        rows.append(f'<tr style="{style}"><td>{escape(name)}</td><td>{escape(how)}</td>'
                    f'<td class="num">{escape(fmt_value(value))}</td></tr>')
    return ('<style>.steps-table{border-collapse:collapse;width:100%;font-size:14px}'
            '.steps-table th,.steps-table td{border:1px solid #DCE8CF;padding:6px 10px;vertical-align:top;text-align:left}'
            '.steps-table th{background:#E6F8CF}.steps-table td.num{text-align:right;white-space:nowrap}</style>'
            '<div style="overflow-x:auto"><table class="steps-table"><tr><th>Что считаем</th>'
            '<th>Откуда берём и как считаем</th><th>Сумма</th></tr>' + "".join(rows) + "</table></div>")


PARAMS_SHEET = "_параметры"


def build_xlsx(p, rows, best, years=None) -> bytes:
    """Файл Excel: исходные данные, сравнение вариантов, расчёт по шагам, патент по годам."""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    # обычный числовой формат без «₽»: ячейки можно складывать формулами, суммы — с копейками
    money, pct = '#,##0.00', '0.0%'
    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="FFF5EC")
    best_fill = PatternFill("solid", fgColor="C9F29B")
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
        ("Входящий НДС к вычету (уже входит в расходы)", p["input_vat"], money),
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
    if p["psn"]:
        s = p["psn"]
        data += [
            ("Потенциальный доход по патенту за год", s["pvd"], money),
            ("Доля выручки на патенте, %", s["share"], "0"),
            ("Сотрудников на патенте", s["staff"], "0"),
            (f"Выручка за {PSN_YEARS[0] - 1} год (УСН и патент)", s["prev_income"], money),
            ("Рост выручки в следующие годы, % в год", s["growth"], "0"),
        ]
    for name, value, fmt in data:
        ws.append([name + (", ₽" if fmt == money else ""), round(value, 2) if isinstance(value, float) else value])
        if fmt:
            ws.cell(ws.max_row, 2).number_format = fmt
    if best:
        ws.append([])
        ws.append(["Самый выгодный вариант", best["Название"]])
        ws.append(["Налоговая нагрузка, ₽", round(best["Итого"], 2)])
        ws.cell(ws.max_row, 2).number_format = money
        ws.append(["Чистая прибыль после налогов, ₽", round(best["Собственнику"], 2)])
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
    cols = [(k, h) for k, h in XLSX_COLUMNS if k not in OPTIONAL or any(r[k] for r in rows)]
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
                line.append(round(r[k], 2) if isinstance(r[k], float) else r[k])
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
    ws = wb.create_sheet("Из чего сложилась сумма")
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 90
    ws.column_dimensions["C"].width = 18
    part_fill = PatternFill("solid", fgColor="E6F8CF")
    for r in ordered:
        ws.append([r["Название"] + ("" if r["Доступен"] else " — недоступен: " + "; ".join(r["Причины"]))])
        ws.cell(ws.max_row, 1).font = Font(bold=True, size=12)
        if best and r["Название"] == best["Название"]:
            ws.cell(ws.max_row, 1).fill = best_fill
        ws.append(["Что считаем", "Откуда берём и как считаем", "Сумма, ₽"])
        for c in range(1, 4):
            ws.cell(ws.max_row, c).font, ws.cell(ws.max_row, c).fill = bold, head_fill
        for name, how, value in r["Шаги"]:
            if isinstance(value, str) and value.endswith("%"):
                ws.append([name, how, round(r["% от дохода"] / 100, 4)])
                ws.cell(ws.max_row, 3).number_format = pct
            else:
                ws.append([name, how, round(value, 2) if isinstance(value, float) else value])
                if isinstance(value, (int, float)):
                    ws.cell(ws.max_row, 3).number_format = money
            row = ws.max_row
            ws.cell(row, 2).alignment = wrap
            for c in range(1, 4):
                ws.cell(row, c).border = border
                if is_part_step(name):
                    ws.cell(row, c).fill = part_fill
                if is_total_step(name):
                    ws.cell(row, c).font = bold
        ws.append([])
    ws.append(["Салатовым выделены суммы, которые входят в налоговую нагрузку."])

    # Лист 4. Патент по годам
    if years:
        ws = wb.create_sheet("Патент по годам")
        ws.append(["Год", "Лимит ПСН, ₽", "Выручка прошлого года, ₽", "Выручка года, ₽", "Патент",
                   "Нагрузка с патентом, ₽", "Лучший вариант с патентом", "Нагрузка на УСН без патента, ₽",
                   "Лучший вариант без патента", "Экономия от патента, ₽"])
        for c in range(1, 11):
            cell = ws.cell(1, c)
            cell.font, cell.fill, cell.alignment, cell.border = bold, head_fill, wrap, border
        ws.row_dimensions[1].height = 45
        for y in years:
            usn = y["usn"]["Итого"] if y["usn"] else None
            psn = None if y["problems"] else y["psn"]["Итого"]
            ws.append([y["year"], y["limit"], round(y["prev"], 2), round(y["income"], 2),
                       "можно" if not y["problems"] else "нельзя: " + "; ".join(y["problems"]),
                       round(psn, 2) if psn is not None else "—",
                       y["psn"]["Режим"] if psn is not None else "—",
                       round(usn, 2) if usn is not None else "—",
                       y["usn"]["Режим"] if y["usn"] else "—",
                       round(usn - psn, 2) if usn is not None and psn is not None else "—"])
            row = ws.max_row
            for c in range(1, 11):
                ws.cell(row, c).border = border
                ws.cell(row, c).alignment = wrap
                if c in (2, 3, 4, 6, 8, 10):
                    ws.cell(row, c).number_format = money
        for c, w in enumerate((8, 16, 18, 18, 50, 18, 34, 18, 34, 18), start=1):
            ws.column_dimensions[get_column_letter(c)].width = w
        ws.append([])
        ws.append([psn_verdict(years)[1]])
        ws.append(["Прогноз: выручка, расходы и ФОТ растут на заданный процент в год; потенциальный доход "
                   "по патенту и ставки взносов — как в 2026 году."])

    # Скрытый лист: из него калькулятор восстанавливает расчёт («Открыть сохранённый расчёт»)
    ws = wb.create_sheet(PARAMS_SHEET)
    for k, v in p["saved"].items():
        ws.append([k, encode(v)])
    ws.sheet_state = "hidden"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def load_xlsx(file) -> dict:
    """Значения полей из файла, скачанного калькулятором; пустой словарь, если листа нет."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(file, read_only=True)
    except Exception:
        return {}
    if PARAMS_SHEET not in wb.sheetnames:
        return {}
    return {k: decode(k, v) for k, v in wb[PARAMS_SHEET].iter_rows(values_only=True) if k in DEFAULTS}


# ---------------------------------------------------------------------------
# Интерфейс
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Калькулятор налогов", page_icon="🧮", layout="wide")


def parse_money(text: str):
    """«22 300 000», «22300000,50», «22 300 000 ₽» → число; None, если это не число."""
    clean = str(text).replace("\u00a0", "").replace(" ", "").replace("₽", "").replace(",", ".")
    try:
        value = float(clean)
    except ValueError:
        return None
    return round(value) if value >= 0 else None


# Значения полей по умолчанию. Всё, что введено, хранится в адресе страницы (?income=...),
# поэтому обновление браузера цифры не сбрасывает; ещё их можно восстановить из скачанного Excel.
DEFAULTS = {
    "income": "12 000 000", "expenses": "5 000 000", "input_vat": "0", "staff": 2, "payroll": "1 200 000",
    "clients_vat": False, "vat_discount": 15, "rate_kind": "Обычная: 6% и 15%",
    "lg_d": None, "lg_dr": None, "lg_ooo": True, "msp": False,
    "psn_on": False, "psn_pvd": "1 000 000", "psn_share": 100, "psn_staff": 0, "prev_income": "12 000 000",
    "growth": 0,
}
RATE_KINDS = ["Обычная: 6% и 15%", "Льготная ставка региона"]


def encode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def decode(key: str, raw):
    """Строка из адреса или Excel → значение поля; при ошибке — значение по умолчанию."""
    default = DEFAULTS[key]
    raw = "" if raw is None else str(raw).strip()
    try:
        if key in ("lg_d", "lg_dr"):
            return None if raw == "" else float(raw.replace(",", "."))
        if isinstance(default, bool):
            return raw in ("1", "True", "true", "да")
        if isinstance(default, int):
            return int(float(raw.replace(",", ".")))
        if key == "rate_kind":
            return raw if raw in RATE_KINDS else default
        value = parse_money(raw)
        return default if value is None else rub(value)[:-2]
    except ValueError:
        return default


ss = st.session_state
if "_saved" not in ss:
    ss["_saved"] = {k: decode(k, st.query_params[k]) if k in st.query_params else d for k, d in DEFAULTS.items()}
for k in DEFAULTS:
    # поле, которое было скрыто (например, льготная ставка), получает прежнее значение обратно
    if k not in ss:
        ss[k] = ss["_saved"][k]


def money_input(label: str, key: str, help: str | None = None) -> int:
    """Поле для суммы: цифры разделяются пробелами по три."""
    def reformat():
        value = parse_money(ss[key])
        if value is not None:
            ss[key] = rub(value)[:-2]

    st.text_input(label, key=key, help=help, on_change=reformat)
    value = parse_money(ss[key])
    if value is None:
        st.error("Введите сумму цифрами, например 22 300 000")
        return 0
    return value


st.title("🧮 Какую систему налогообложения выбрать")
st.caption("ИП и ООО: УСН, ОСНО, АУСН, патент. Правила 2026 года. Расчёт ориентировочный — "
           "итоговое решение принимает бухгалтер с учётом всех обстоятельств.")

with st.sidebar:
    st.header("Исходные данные")
    with st.expander("📂 Открыть сохранённый расчёт"):
        st.caption("Выберите файл Excel, который раньше скачали из калькулятора, — все цифры подставятся сами.")
        uploaded = st.file_uploader("Файл расчёта", type=["xlsx"], label_visibility="collapsed")
        if uploaded is not None and ss.get("_loaded_file") != uploaded.file_id:
            loaded = load_xlsx(uploaded)
            if loaded:
                for k, v in loaded.items():
                    ss[k] = ss["_saved"][k] = v
                st.success("Расчёт загружен.")
            else:
                st.error("В этом файле нет сохранённых данных калькулятора. "
                         "Подходит только файл, скачанный кнопкой «Скачать расчёт в Excel».")
            ss["_loaded_file"] = uploaded.file_id

    income = money_input("Выручка за год (с НДС), ₽", "income",
                         help="Всё, что платят клиенты за год, включая НДС, если он есть.")
    expenses = money_input("Расходы за год, ₽", "expenses",
                           help="Без зарплаты и взносов — их укажите ниже.")
    input_vat = money_input("в т.ч. входящий НДС к вычету, ₽", "input_vat",
                            help="НДС из счетов поставщиков, которые работают с НДС 22%. Эта сумма уже входит "
                                 "в «Расходы за год». Её принимают к вычету на ОСНО и на УСН со ставкой НДС 22%. "
                                 "На УСН с НДС 5% или 7% вычета нет. Если поставщики без НДС — оставьте 0.")
    max_input_vat = expenses * VAT / (1 + VAT)
    if input_vat > max_input_vat:
        st.warning(f"Входящий НДС не может быть больше {rub(max_input_vat)}: столько НДС было бы, "
                   "если бы все расходы были от поставщиков с НДС 22%.")
    elif expenses and input_vat:
        st.caption(f"Это {input_vat / max_input_vat * 100:.0f}% от максимально возможного НДС в ваших расходах "
                   f"({rub(max_input_vat)}).")
    staff = st.number_input("Количество сотрудников", min_value=0, step=1, key="staff")
    payroll = money_input("Фонд оплаты труда за год, ₽", "payroll",
                          help="Начисленная зарплата до вычета НДФЛ, без взносов.")
    clients_vat = st.checkbox("Клиентам важен входящий НДС", key="clients_vat",
                              help="Клиенты — компании на ОСНО, которым нужен вычет НДС.")
    vat_discount = 0
    if clients_vat:
        vat_discount = st.slider("Скидка, которую потребуют клиенты без НДС, %", 0, 25, key="vat_discount",
                                 help="Насколько придётся снизить цену, если вы не выставляете НДС 22%.")

    st.subheader("Ставка УСН")
    rate_kind = st.radio("Ставка УСН", RATE_KINDS, key="rate_kind", label_visibility="collapsed",
                         help="Регион своим законом может снизить ставку УСН. Например, в Санкт-Петербурге.")
    usn_rates = {"ИП": (6.0, "", 15.0, ""), "ООО": (6.0, "", 15.0, "")}
    if rate_kind == "Льготная ставка региона":
        lg_d = st.number_input("Льготная ставка УСН «Доходы», %", min_value=0.0, max_value=6.0,
                               value=None, step=0.5, placeholder="введите ставку региона", key="lg_d")
        lg_dr = st.number_input("Льготная ставка УСН «Доходы минус расходы», %", min_value=0.0,
                                max_value=15.0, value=None, step=0.5, placeholder="введите ставку региона",
                                key="lg_dr")
        lg_ooo = st.checkbox("Льгота действует и для ООО", key="lg_ooo",
                             help="Снимите галочку, если по закону региона льгота только для ИП.")
        if lg_d is None and lg_dr is None:
            st.caption("Введите ставку хотя бы по одному объекту. Пока поле пустое, считается обычная ставка.")
        lg = (6.0 if lg_d is None else lg_d, "" if lg_d is None else " (льгота)",
              15.0 if lg_dr is None else lg_dr, "" if lg_dr is None else " (льгота)")
        usn_rates["ИП"] = lg
        if lg_ooo:
            usn_rates["ООО"] = lg

    with st.expander("Тарифы взносов"):
        msp = st.checkbox("Льготный тариф взносов МСП (15% свыше 1,5 МРОТ)", key="msp",
                          help="С 2026 года льгота сохранена не для всех отраслей — уточните по своему ОКВЭД.")

    st.subheader("Патент")
    psn = None
    if st.checkbox("Посчитать патент для ИП", key="psn_on",
                   help="Патент на часть или всю выручку, остальное — на УСН. Плюс проверка права "
                        "на патент в 2026, 2027 и 2028 годах."):
        pvd = money_input("Потенциальный доход по патенту за год, ₽", "psn_pvd",
                          help="Из закона субъекта о ПСН на 12 месяцев, по всем патентам вместе. "
                               "Налог по патенту = 6% от этой суммы.")
        share = st.slider("Доля выручки на патенте, %", 0, 100, key="psn_share",
                          help="Какая часть выручки идёт от деятельности на патенте. Остальное — УСН.")
        st.caption(f"По патенту {rub(income * share / 100)}, по УСН {rub(income * (1 - share / 100))}. "
                   "Расходы делятся по той же доле.")
        psn_staff = st.number_input("Из них сотрудников на патенте", min_value=0, step=1, key="psn_staff",
                                    help=f"Не больше {PSN_STAFF}. Если есть — патент уменьшается на взносы "
                                         "не больше чем на 50%.")
        prev_income = money_input(f"Выручка за {PSN_YEARS[0] - 1} год, ₽", "prev_income",
                                  help="УСН и патент вместе. По ней проверяется право на патент в 2026 году.")
        growth = st.number_input("Рост выручки в 2027 и 2028 годах, % в год", min_value=-90, max_value=500,
                                 step=5, key="growth",
                                 help="Для прогноза: на сколько процентов в год вырастет выручка. 0 — останется как сейчас.")
        psn = dict(pvd=pvd, share=share, staff=int(psn_staff), prev_income=prev_income, growth=growth)

    st.caption("💾 Введённые цифры сохраняются в адресе страницы: если обновить браузер, они не пропадут. "
               "Чтобы вернуться к расчёту позже, добавьте страницу в закладки или скачайте расчёт в Excel.")

# запоминаем введённое: в памяти сессии и в адресе страницы
for k in DEFAULTS:
    if k in ss:
        ss["_saved"][k] = ss[k]
st.query_params.from_dict({k: encode(v) for k, v in ss["_saved"].items() if v != DEFAULTS[k]})

input_vat = min(input_vat, expenses * VAT / (1 + VAT))
p = dict(income=income, expenses=expenses, payroll=payroll, staff=int(staff), clients_vat=clients_vat,
         vat_discount=vat_discount, usn_rates=usn_rates, msp=msp, input_vat=input_vat, psn=psn,
         saved=dict(ss["_saved"]))

if income <= 0:
    st.info("Введите выручку за год в панели слева.")
    st.stop()

rows = calc_all(p)
available = [r for r in rows if r["Доступен"]]
best = min(available, key=lambda r: r["Итого"]) if available else None
years = psn_forecast(p) if psn else None

if best:
    c1, c2, c3, c4 = st.columns(4)
    # своя плашка вместо st.metric: длинное название режима переносится, а не обрезается многоточием
    c1.markdown(f'<div style="font-size:14px;margin-bottom:4px">Самый выгодный режим</div>'
                f'<div style="font-size:21px;font-weight:600;line-height:1.3;color:#0B4D12">'
                f'{best["Название"]}</div>', unsafe_allow_html=True)
    c2.metric("Прибыль до налогов", rub(best["Прибыль до налогов"]),
              help="Выручка с НДС минус расходы и зарплата сотрудников. Одинакова для всех вариантов.")
    c3.metric("Налоговая нагрузка", rub(best["Итого"]), f"{best['% от дохода']:.1f}% от выручки", delta_color="off")
    c4.metric("Чистая прибыль после налогов", rub(best["Собственнику"]),
              help="Прибыль до налогов минус налоговая нагрузка. У ООО — после НДФЛ с дивидендов.")

if years:
    level, text = psn_verdict(years)
    getattr(st, level)("**Патент.** " + text)

tab_cmp, tab_psn, tab_detail, tab_warn, tab_about = st.tabs(
    ["📊 Сравнение", "🗓 Патент по годам", "🔍 Подробный расчёт", "⚠️ Предупреждения", "ℹ️ Допущения"])

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
        "Патент": "Налог по патенту",
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
    table = table.drop(columns=[c for c in OPTIONAL if not table[c].ne("0").any()])
    table = table.rename(columns=names_map)

    def highlight(row):
        if row["Вариант"] == best_name:
            return ["background-color: #C9F29B; color: #0B4D12; font-weight: 600"] * len(row)
        if row["Доступен ли"].startswith("⛔"):
            return ["color: #999999"] * len(row)
        return [""] * len(row)

    # Обычная HTML-таблица: заголовки и названия вариантов переносятся по словам, ничего не обрезается
    styled = (table.style.apply(highlight, axis=1).hide(axis="index")
              .set_table_attributes('class="cmp-table"'))
    st.markdown("""
<style>
.cmp-wrap {overflow-x: auto;}
.cmp-table {border-collapse: collapse; width: 100%; font-size: 14px;}
.cmp-table th, .cmp-table td {border: 1px solid #DCE8CF; padding: 6px 8px; vertical-align: top;}
.cmp-table th {background: #E6F8CF; font-weight: 600; text-align: left; white-space: normal;
               word-break: normal; overflow-wrap: break-word; min-width: 90px;}
.cmp-table td {text-align: right; white-space: nowrap;}
.cmp-table td:first-child {text-align: left; white-space: normal; min-width: 220px;}
.cmp-table td:last-child {text-align: left; white-space: normal;}
</style>
""", unsafe_allow_html=True)
    # без отступов и пустых строк, иначе Markdown примет часть таблицы за блок кода
    html = "".join(line.strip() for line in styled.to_html().splitlines())
    st.markdown(f'<div class="cmp-wrap">{html}</div>', unsafe_allow_html=True)

    try:
        xlsx = build_xlsx(p, rows, best, years)
    except ImportError:
        st.warning("Для скачивания в Excel поставьте библиотеку: закройте калькулятор (Ctrl+C в чёрном окне), "
                   "выполните `pip install openpyxl` и снова `streamlit run app.py`.")
    else:
        st.download_button("📥 Скачать расчёт в Excel", xlsx, file_name="Расчёт налогов.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           type="primary")
        st.caption("В файле три листа: исходные данные, сравнение всех вариантов и «Из чего сложилась сумма» — расчёт по шагам для каждого варианта: откуда берётся число, на что умножается и что вычитается.")

with tab_psn:
    if not years:
        st.info("Включите «Посчитать патент для ИП» в панели слева: появятся варианты «ПСН + УСН» в сравнении "
                "и проверка права на патент по годам.")
    else:
        st.markdown(f"**Лимит выручки для патента** (УСН и патент вместе): {rub(PSN_LIMITS[2026])} в 2026 году, "
                    f"{rub(PSN_LIMITS[2027])} в 2027 году, {rub(PSN_LIMITS[2028])} с 2028 года. Проверяется дважды: "
                    "выручка за прошлый год — можно ли получить патент, и выручка с начала текущего года — "
                    "не слетит ли клиент в течение года. Если лимит превышен в течение года, право теряется "
                    "с начала срока патента: налог за весь срок пересчитывают по УСН, а уплаченный патент "
                    "засчитывают в налог УСН. Новый патент — не раньше следующего года.")
        g = psn["growth"]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Выручка года" + (f" (прогноз +{g}% в год)" if g > 0 else f" (прогноз {g}% в год)" if g else ""),
            x=[str(y["year"]) for y in years], y=[y["income"] / 1_000_000 for y in years],
            marker=dict(color=["#12A33A" if not y["problems"] else "#D9534F" for y in years]),
            customdata=[y["income"] for y in years], hovertemplate="%{x}: %{customdata:,.0f} ₽<extra></extra>"))
        fig.add_trace(go.Scatter(
            name="Лимит ПСН", x=[str(y["year"]) for y in years], y=[y["limit"] / 1_000_000 for y in years],
            mode="lines+markers", line=dict(color="#333333", dash="dash", shape="hvh"),
            hovertemplate="Лимит %{x}: %{y:,.0f} млн ₽<extra></extra>"))
        fig.update_layout(title="Выручка и лимит патента по годам: зелёный — патент можно, красный — нельзя",
                          yaxis=dict(title="млн ₽", tickformat=",.1f"), height=360, separators=", ",
                          legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                          margin=dict(l=10, r=10, t=90, b=10))
        st.plotly_chart(fig, width="stretch")

        lines = []
        for y in years:
            psn_total = None if y["problems"] else y["psn"]["Итого"]
            usn_total = y["usn"]["Итого"] if y["usn"] else None
            lines.append({
                "Год": str(y["year"]),
                "Лимит ПСН": rub(y["limit"]),
                "Выручка прошлого года": rub(y["prev"]),
                "Выручка года": rub(y["income"]),
                "Патент": "✅ можно" if not y["problems"] else "⛔ " + "; ".join(y["problems"]),
                "Нагрузка с патентом": (f"{rub(psn_total)} ({y['psn']['Режим']})"
                                        if psn_total is not None else "—"),
                "Нагрузка на УСН без патента": (f"{rub(usn_total)} ({y['usn']['Режим']})"
                                                if usn_total is not None else "—"),
                "Экономия от патента": (rub(usn_total - psn_total)
                                        if psn_total is not None and usn_total is not None else "—"),
            })
        html = pd.DataFrame(lines).style.hide(axis="index").set_table_attributes('class="cmp-table"').to_html()
        st.markdown('<div class="cmp-wrap">' + "".join(l.strip() for l in html.splitlines()) + "</div>",
                    unsafe_allow_html=True)
        st.caption("Прогноз на 2027–2028 годы: выручка, расходы и ФОТ растут на заданный процент в год, выручка "
                   "идёт равномерно по месяцам. Потенциальный доход по патенту и ставки взносов взяты как в 2026 "
                   "году — на 2027–2028 годы они ещё не утверждены. Подробный расчёт 2026 года — в сравнении "
                   "и на вкладке «Подробный расчёт», варианты «ИП · ПСН + УСН».")

with tab_detail:
    names = [r["Название"] for r in rows]
    default = names.index(best["Название"]) if best else 0
    pick = st.selectbox("Режим", names, index=default)
    r = next(x for x in rows if x["Название"] == pick)
    if not r["Доступен"]:
        st.error("Режим недоступен: " + "; ".join(r["Причины"]))
    st.markdown("Расчёт по шагам на ваших цифрах: откуда берётся каждое число и на что умножается. "
                "**Салатовым** выделены суммы, которые складываются в налоговую нагрузку.")
    st.markdown(steps_html(r["Шаги"]), unsafe_allow_html=True)
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
- Патент (только ИП, совмещение с УСН): налог 6% от потенциального дохода по закону субъекта на 12 месяцев.
  Лимит выручки по УСН и патенту вместе — {rub(PSN_LIMITS[2026])} в 2026 году, {rub(PSN_LIMITS[2027])} в 2027,
  {rub(PSN_LIMITS[2028])} с 2028 года, проверяется за прошлый год и с начала текущего; на патенте не больше
  {PSN_STAFF} сотрудников. Взнос 1% считается с потенциального дохода по патенту плюс доход по УСН.
  Взносы ИП за себя делятся между патентом и УСН пропорционально выручке, расходы — тоже.
  Патент уменьшается на взносы полностью, если на нём нет сотрудников, иначе — не больше чем на 50%.
  Налог по патенту НДС не облагается, освобождение от НДС на УСН — по выручке обоих режимов.

**Почему цифры могут не совпасть с ручным расчётом в Excel**
- ИП на ОСНО: НДФЛ считается по прогрессивной шкале 13 / 15 / 18 / 20 / 22%, а не ровно 18% со всей прибыли.
- ООО: в нагрузку входит НДФЛ с дивидендов — собственник забирает прибыль только после этого налога.
  Поэтому показаны обе цифры: чистая прибыль компании и чистая прибыль собственнику.
- Взносы ИП за себя: {rub(IP_FIXED)} + 1% с суммы свыше {rub(IP_1PCT_THRESHOLD)}. {rub(IP_1PCT_CAP)} — это потолок
  только для 1%, а не сумма взносов.
- Уменьшение налога на взносы не бывает отрицательным: налог уменьшается на взносы, но не больше лимита (100% или 50%).

**Чего калькулятор не учитывает:** зарплату собственника ООО вместо дивидендов, патент вместе с ОСНО,
патент на часть года, торговый сбор,
имущественные налоги, региональные льготы кроме ставки УСН, освобождение от НДС по ст. 145 НК РФ на ОСНО.
""")
