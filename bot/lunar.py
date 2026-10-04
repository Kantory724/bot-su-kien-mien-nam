"""Đổi ngày âm lịch Việt Nam (múi giờ UTC+7) sang dương lịch. Thuật toán Hồ Ngọc Đức."""
from datetime import date
from math import pi, sin

_SYN = 29.530588853
_EPOCH = 2415021.076998695


def _jd(dd: int, mm: int, yy: int) -> int:
    a = (14 - mm) // 12
    y, m = yy + 4800 - a, mm + 12 * a - 3
    jd = dd + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    return jd if jd >= 2299161 else dd + (153 * m + 2) // 5 + 365 * y + y // 4 - 32083


def _from_jd(jd: int) -> date:
    if jd > 2299160:
        a = jd + 32044
        b = (4 * a + 3) // 146097
        c = a - (b * 146097) // 4
    else:
        b, c = 0, jd + 32082
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    return date(b * 100 + d - 4800 + m // 10, m + 3 - 12 * (m // 10), e - (153 * m + 2) // 5 + 1)


def _new_moon(k: int) -> float:
    T = k / 1236.85
    T2, T3, dr = T * T, T ** 3, pi / 180
    jd1 = 2415020.75933 + 29.53058868 * k + 0.0001178 * T2 - 0.000000155 * T3
    jd1 += 0.00033 * sin((166.56 + 132.87 * T - 0.009173 * T2) * dr)
    M = 359.2242 + 29.10535608 * k - 0.0000333 * T2 - 0.00000347 * T3
    Mp = 306.0253 + 385.81691806 * k + 0.0107306 * T2 + 0.00001236 * T3
    F = 21.2964 + 390.67050646 * k - 0.0016528 * T2 - 0.00000239 * T3
    c1 = ((0.1734 - 0.000393 * T) * sin(M * dr) + 0.0021 * sin(2 * dr * M) - 0.4068 * sin(Mp * dr)
          + 0.0161 * sin(dr * 2 * Mp) - 0.0004 * sin(dr * 3 * Mp) + 0.0104 * sin(dr * 2 * F)
          - 0.0051 * sin(dr * (M + Mp)) - 0.0074 * sin(dr * (M - Mp)) + 0.0004 * sin(dr * (2 * F + M))
          - 0.0004 * sin(dr * (2 * F - M)) - 0.0006 * sin(dr * (2 * F + Mp)) + 0.0010 * sin(dr * (2 * F - Mp))
          + 0.0005 * sin(dr * (2 * Mp + M)))
    dt = (0.001 + 0.000839 * T + 0.0002261 * T2 - 0.00000845 * T3 - 0.000000081 * T * T3) if T < -11 \
        else (-0.000278 + 0.000265 * T + 0.000262 * T2)
    return jd1 + c1 - dt


def _sun_long(jdn: float) -> float:
    T = (jdn - 2451545.0) / 36525
    T2, dr = T * T, pi / 180
    M = 357.52910 + 35999.05030 * T - 0.0001559 * T2 - 0.00000048 * T * T2
    L0 = 280.46645 + 36000.76983 * T + 0.0003032 * T2
    dl = ((1.914600 - 0.004817 * T - 0.000014 * T2) * sin(dr * M) + (0.019993 - 0.000101 * T) * sin(2 * dr * M)
          + 0.000290 * sin(3 * dr * M))
    L = (L0 + dl) * dr
    return L - pi * 2 * int(L / (pi * 2))


def _nm_day(k: int, tz: int) -> int:
    return int(_new_moon(k) + 0.5 + tz / 24)


def _sun_idx(jdn: int, tz: int) -> int:
    return int(_sun_long(jdn - 0.5 - tz / 24) / pi * 6)


def _month11(yy: int, tz: int) -> int:
    k = int((_jd(31, 12, yy) - 2415021) / _SYN)
    nm = _nm_day(k, tz)
    return _nm_day(k - 1, tz) if _sun_idx(nm, tz) >= 9 else nm


def _leap_offset(a11: int, tz: int) -> int:
    k = int((a11 - _EPOCH) / _SYN + 0.5)
    i = 1
    arc = _sun_idx(_nm_day(k + i, tz), tz)
    while True:
        last, i = arc, i + 1
        arc = _sun_idx(_nm_day(k + i, tz), tz)
        if not (arc != last and i < 14):
            return i - 1


def lunar_to_solar(day: int, month: int, year: int, leap: bool = False, tz: int = 7) -> date | None:
    """Âm lịch -> dương lịch. Trả None nếu ngày không hợp lệ."""
    try:
        if not (1 <= day <= 30 and 1 <= month <= 12 and 1900 <= year <= 2100):
            return None
        if month < 11:
            a11, b11 = _month11(year - 1, tz), _month11(year, tz)
        else:
            a11, b11 = _month11(year, tz), _month11(year + 1, tz)
        k = int(0.5 + (a11 - _EPOCH) / _SYN)
        off = month - 11 + (12 if month < 11 else 0)
        if b11 - a11 > 365:
            leap_off = _leap_offset(a11, tz)
            leap_month = (leap_off - 2) % 12
            if leap and month != leap_month:
                return None
            if leap or off >= leap_off:
                off += 1
        return _from_jd(_nm_day(k + off, tz) + day - 1)
    except Exception:  # noqa
        return None
