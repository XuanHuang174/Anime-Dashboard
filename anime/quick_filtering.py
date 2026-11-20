"""Year restrictions are AND conditions, independent of tag match mode."""
import re
from datetime import date


def airing_year(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'([1-9][0-9]{3})(?:[-/](0?[1-9]|1[0-2])(?:[-/](0?[1-9]|[12]\d|3[01]))?|年(0?[1-9]|1[0-2])月)?', value.strip())
    if not match:
        return None
    if match[3]:
        try:
            date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            return None
    return int(match[1])


def matches_year_range(period, year_from, year_to):
    if year_from is None and year_to is None:
        return True
    year = airing_year(period)
    return year is not None and (year_from is None or year >= year_from) and (year_to is None or year <= year_to)
