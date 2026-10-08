# SPDX-License-Identifier: Apache-2.0
"""Repeat choices serialize to the existing task store's iCalendar RRULE."""

CHOICES = (
    ('none', 'Never', ''),
    ('daily', 'Every day', 'FREQ=DAILY'),
    ('weekdays', 'Every weekday', 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR'),
    ('weekly', 'Every week', 'FREQ=WEEKLY'),
    ('monthly', 'Every month', 'FREQ=MONTHLY'),
    ('yearly', 'Every year', 'FREQ=YEARLY'),
)
UNITS = (('DAILY', 'Days'), ('WEEKLY', 'Weeks'), ('MONTHLY', 'Months'), ('YEARLY', 'Years'))


def interval_rule(interval, frequency):
    words = str(interval).strip()
    if not words.isdecimal() or not 1 <= int(words) <= 365:
        raise ValueError('Use a whole number from 1 to 365.')
    if frequency not in dict(UNITS):
        raise ValueError('Choose days, weeks, months or years.')
    return f'FREQ={frequency};INTERVAL={int(words)}'


def label(rule):
    return next((name for _, name, value in CHOICES if value == (rule or '')), 'Custom repeat')


def custom_rule(original, interval, frequency):
    """Edit only the visible controls; keep imported limits and day filters."""
    basic = interval_rule(interval, frequency)
    parts = str(original or '').split(';') if original else []
    fields = dict(part.split('=', 1) for part in parts if '=' in part)
    new_interval = str(int(str(interval).strip()))
    if (parts and fields.get('FREQ') == frequency
            and fields.get('INTERVAL', '1') == new_interval):
        return original
    if not parts:
        return basic
    result = []
    for part in parts:
        key, separator, value = part.partition('=')
        if key == 'FREQ': value = frequency
        elif key == 'INTERVAL': value = new_interval
        result.append(key + separator + value)
    if 'FREQ' not in fields: result.insert(0, 'FREQ=' + frequency)
    if 'INTERVAL' not in fields: result.append('INTERVAL=' + new_interval)
    return ';'.join(result)
