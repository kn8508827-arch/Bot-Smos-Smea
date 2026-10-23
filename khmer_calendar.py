"""Khmer lunar calendar (ចន្ទគតិ) — ported from the MIT-licensed momentkh library
(github.com/ThyrithSor/momentkh), which follows the traditional chhankitek rules.

  gregorian_to_khmer(date) -> KhmerDay(day, waning, month_index, be_year, animal_index, ...)
  format_khmer(date)       -> "ថ្ងៃសុក្រ ៨រោច ខែអស្សុជ ឆ្នាំម្សាញ់ សប្តស័ក ព.ស. ២៥៧០"
  is_sila_day(date)        -> ថ្ងៃសីល (8 & 15 កើត/រោច, and the last day of a 29-day month)
"""
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

LUNAR_MONTHS = ['មិគសិរ', 'បុស្ស', 'មាឃ', 'ផល្គុន', 'ចេត្រ', 'ពិសាខ', 'ជេស្ឋ', 'អាសាឍ',
                'ស្រាពណ៍', 'ភទ្របទ', 'អស្សុជ', 'កត្ដិក', 'បឋមាសាឍ', 'ទុតិយាសាឍ']
SOLAR_MONTHS = ['មករា', 'កុម្ភៈ', 'មីនា', 'មេសា', 'ឧសភា', 'មិថុនា', 'កក្កដា', 'សីហា',
                'កញ្ញា', 'តុលា', 'វិច្ឆិកា', 'ធ្នូ']
ANIMALS = ['ជូត', 'ឆ្លូវ', 'ខាល', 'ថោះ', 'រោង', 'ម្សាញ់', 'មមី', 'មមែ', 'វក', 'រកា', 'ច', 'កុរ']
ANIMAL_EMOJI = ['🐀', '🐂', '🐅', '🐇', '🐉', '🐍', '🐎', '🐐', '🐒', '🐓', '🐕', '🐖']
SAKS = ['សំរឹទ្ធិស័ក', 'ឯកស័ក', 'ទោស័ក', 'ត្រីស័ក', 'ចត្វាស័ក', 'បញ្ចស័ក', 'ឆស័ក', 'សប្តស័ក', 'អដ្ឋស័ក', 'នព្វស័ក']
WEEKDAYS = ['អាទិត្យ', 'ចន្ទ', 'អង្គារ', 'ពុធ', 'ព្រហស្បតិ៍', 'សុក្រ', 'សៅរ៍']   # 0 = Sunday
PHASES = ['កើត', 'រោច']
JESTH, PISAKH, CHEIT, KADEUK, MIGASIR, SRAP, PATHAM, TUTIYA = 6, 5, 4, 11, 0, 8, 12, 13


def kh_num(n):
    return str(n).translate(str.maketrans("0123456789", "០១២៣៤៥៦៧៨៩"))


# ---------- chhankitek ----------

def _aharkun(be):
    return (be * 292207 + 499) // 800 + 4


def _kromthupul(be):
    return 800 - ((be * 292207 + 499) % 800)


def _avoman(be):
    return (_aharkun(be) * 11 + 25) % 692


def _bodithey(be):
    a = _aharkun(be)
    return ((a * 11 + 25) // 692 + a + 29) % 30


def _leap_day_calc(be):
    av, solar_leap = _avoman(be), _kromthupul(be) <= 207
    if av == 0 and _avoman(be - 1) == 137:
        return True
    if solar_leap:
        return av < 127
    if av == 137 and _avoman(be + 1) == 0:
        return False
    return av < 138


def _leap_month(be):
    b, nb = _bodithey(be), _bodithey(be + 1)
    if b == 25 and nb == 5:
        return False
    return (b == 24 and nb == 6) or b >= 25 or b < 6


@lru_cache(maxsize=512)
def leap_type(be):
    """1 = អធិកមាស (13 months), 2 = ចន្ទ្រាធិមាស (ជេស្ឋ has 30 days), 0 = normal."""
    if _leap_month(be):
        return 1
    if _leap_day_calc(be):
        return 2
    if _leap_month(be - 1):
        prev = be - 1
        while True:
            if _leap_day_calc(prev):
                return 2
            prev -= 1
            if not _leap_month(prev):
                return 0
    return 0


def days_in_month(m, be):
    lt = leap_type(be)
    if m == JESTH and lt == 2:
        return 30
    if m in (PATHAM, TUTIYA):
        return 30 if lt == 1 else 0
    return 29 if m % 2 == 0 else 30


def days_in_year(be):
    return {1: 384, 2: 355}.get(leap_type(be), 354)


def next_month(m, be):
    if m == JESTH and leap_type(be) == 1:
        return PATHAM
    if m == KADEUK:
        return MIGASIR
    if m == PATHAM:
        return TUTIYA
    if m == TUTIYA:
        return SRAP
    return m + 1


def _maybe_be(year, month):
    return year + 543 if month <= 4 else year + 544


@dataclass
class KhmerDay:
    day: int            # 1..15
    waning: bool        # False = កើត, True = រោច
    month: int          # index into LUNAR_MONTHS
    be_year: int
    animal: int
    sak: int
    weekday: int        # 0 = Sunday
    month_days: int

    @property
    def day_number(self):   # 0..29
        return self.day - 1 + (15 if self.waning else 0)


def _lunar(d):
    """(day_number 0..29, month index) for a Gregorian date."""
    epoch = date(1900, 1, 1)
    month, diff = 1, (d - epoch).days        # 1 Jan 1900 = ១កើត ខែបុស្ស
    if diff > 0:
        while True:
            nxt = days_in_year(_maybe_be(epoch.year + 1, epoch.month))
            if diff > nxt:
                diff -= nxt
                epoch += timedelta(days=nxt)
            else:
                break
    else:
        while diff < 0:
            cur = days_in_year(_maybe_be(epoch.year, epoch.month))
            diff += cur
            epoch -= timedelta(days=cur)
    while diff > 0:
        be = _maybe_be(epoch.year, epoch.month)
        dim = days_in_month(month, be)
        if diff > dim:
            diff -= dim
            epoch += timedelta(days=dim)
            month = next_month(month, be)
        else:
            break
    be = _maybe_be(d.year, d.month)
    total = days_in_month(month, be)
    if diff >= total:
        diff %= total
        month = next_month(month, be)
    return diff, month


@lru_cache(maxsize=64)
def visakha_bochea(year):
    """Date of ១រោច ខែពិសាខ (BE year changes that day)."""
    d = date(year, 4, 1)
    while d < date(year, 7, 1):
        num, m = _lunar(d)
        if m == PISAKH and num == 15:
            return d
        d += timedelta(days=1)
    return date(year, 4, 15)


def gregorian_to_khmer(d):
    num, month = _lunar(d)
    be = d.year + 544 if d >= visakha_bochea(d.year) else d.year + 543
    animal = ((be + 4) % 12 + 12) % 12
    js = be - 1182
    # animal year & era change at Khmer New Year (about 14 April), before the BE year changes
    if date(d.year, 4, 14) <= d < visakha_bochea(d.year):
        animal = (animal + 1) % 12
    if date(d.year, 4, 16) <= d < visakha_bochea(d.year):
        js += 1
    return KhmerDay(day=num % 15 + 1, waning=num >= 15, month=month, be_year=be, animal=animal,
                    sak=js % 10, weekday=(d.isoweekday() % 7), month_days=days_in_month(month, _maybe_be(d.year, d.month)))


def is_sila_day(d):
    k = gregorian_to_khmer(d)
    if k.day in (8, 15):
        return True
    return k.waning and k.day == 14 and k.month_days == 29


def format_khmer(d, with_weekday=True):
    k = gregorian_to_khmer(d)
    text = (f"{kh_num(k.day)}{PHASES[int(k.waning)]} ខែ{LUNAR_MONTHS[k.month]} "
            f"ឆ្នាំ{ANIMALS[k.animal]} {SAKS[k.sak]} ព.ស. {kh_num(k.be_year)}")
    return (f"ថ្ងៃ{WEEKDAYS[k.weekday]} " + text) if with_weekday else text


def format_solar(d):
    return f"{kh_num(d.day)} {SOLAR_MONTHS[d.month - 1]} {kh_num(d.year)}"
