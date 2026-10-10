#!/usr/bin/env python3
"""Fetch Anthropic Careers jobs and print non-overlapping date groups.

Standalone: requires only Python 3.8+ and internet access.
Dates use Greenhouse's first-publication timestamp for each job post.
The feed has no structured seniority, degree, or employment-type filters.
"""

import argparse
from datetime import datetime
import json
import os
import shutil
import sys
import time
import textwrap
import urllib.request


DAY_WINDOWS = (1, 3, 7, 14, 30)
SECONDS_PER_DAY = 86400


SEARCH_URL = 'https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=true'

# Empty tuples include every value. Matching is case-insensitive.
DEPARTMENTS = ()
LOCATION_TYPES = ()
TITLE_INCLUDE = ()  # Match any of these title substrings.
TITLE_EXCLUDE = ()  # Exclude any of these title substrings.


def collect(*, since=None):
    """Fetch London postings, including jobs with multiple offices.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    request = urllib.request.Request(SEARCH_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
        raise ValueError('Anthropic job feed could not be recognised.')
    if data.get('meta', {}).get('total', len(data['jobs'])) != len(data['jobs']):
        raise ValueError('Incomplete Anthropic job feed. Please run the script again.')
    jobs = []
    seen = set()
    for raw in data['jobs']:
        try:
            offices = [office['name'] for office in raw['offices']]
            locations = [part.strip() for part in
                         raw['location']['name'].replace('|', ';').split(';')]
            if 'London, UK' not in offices + locations:
                continue
            if any(not isinstance(raw[key], str) or not raw[key].strip()
                   for key in ('title', 'absolute_url', 'first_published')):
                raise ValueError('Missing job title, URL, or first-publication date.')
            departments = [entry['name'] for entry in raw['departments']]
            location_types = [entry['value'] for entry in raw.get('metadata') or []
                              if entry['name'] == 'Location Type' and entry['value']]
            if (DEPARTMENTS and not any(value.casefold() in
                    {name.casefold() for name in departments} for value in DEPARTMENTS)):
                continue
            if (LOCATION_TYPES and not any(value.casefold() in
                    {name.casefold() for name in location_types} for value in LOCATION_TYPES)):
                continue
            title = raw['title'].strip()
            if TITLE_INCLUDE and not any(value.casefold() in title.casefold()
                                         for value in TITLE_INCLUDE):
                continue
            if any(value.casefold() in title.casefold() for value in TITLE_EXCLUDE):
                continue
            stamp = datetime.fromisoformat(raw['first_published'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('First-publication date has no timezone.')
            if raw['id'] in seen:
                raise ValueError('Duplicate jobs in the feed. Please run the script again.')
            seen.add(raw['id'])
            jobs.append({'title': title, 'url': raw['absolute_url'],
                         'published': stamp.timestamp()})
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError('Anthropic job data format changed.') from error
    if since is not None:
        jobs = [job for job in jobs if job['published'] is not None
                and since <= job['published'] <= since + SECONDS_PER_DAY]
    return sorted(jobs, key=lambda job: job['published'], reverse=True)


def filter_summary():
    parts = ['All employment types, levels and degrees']
    for label, values in [('Teams', DEPARTMENTS), ('Location types', LOCATION_TYPES),
                          ('Title includes', TITLE_INCLUDE), ('Title excludes', TITLE_EXCLUDE)]:
        if values:
            parts.append(label + ': ' + ', '.join(values))
    return ' / '.join(parts)


def format_age(seconds):
    """Show elapsed whole hours below one day, then elapsed whole days."""
    if seconds < 3600:
        return 'Less than 1 Hour Ago'
    if seconds < SECONDS_PER_DAY:
        count, unit = int(seconds // 3600), 'Hour'
    else:
        count, unit = int(seconds // SECONDS_PER_DAY), 'Day'
    return f"{count} {unit}{'' if count == 1 else 's'} Ago"


def format_jobs(jobs, now, *, colour=False, width=88, last24=False, skip_empty=False):
    """Format titles and URLs, placing each job in its youngest matching group.

    Args:
        jobs: Anthropic job records containing title, url, and published timestamp.
        now: The current Unix timestamp used for all age comparisons.
        colour: Whether to include ANSI terminal colours.
        width: Terminal width used to wrap prose, but never job URLs.
        last24: Show only jobs from the inclusive last 24 hours.
        skip_empty: Omit empty reports and date groups.

    Returns:
        A terminal report containing the selected non-overlapping date groups.
    """
    windows = (1,) if last24 else DAY_WINDOWS
    if last24:
        jobs = [job for job in jobs if job['published'] is not None
                and 0 <= now - job['published'] <= SECONDS_PER_DAY]
    groups = {days: [] for days in windows}
    for job in sorted(jobs, key=lambda job: job['published'], reverse=True):
        age = now - job['published']
        if age < 0:
            continue
        for days in windows:
            if age <= days * SECONDS_PER_DAY:
                groups[days].append(job)
                break

    def styled(text, code):
        return f'\033[{code}m{text}\033[0m' if colour else text

    width = max(24, min(width, 96))

    def prose(text, indent=0):
        return textwrap.fill(text, width=width, initial_indent=' ' * indent,
                             subsequent_indent=' ' * indent)

    total = sum(len(matches) for matches in groups.values())
    if skip_empty and total == 0:
        return ''
    checked = datetime.fromtimestamp(now).astimezone()
    rule = styled('-' * width, '2')
    period = '24 hours' if last24 else '30 days'
    lines = [
        styled('ANTHROPIC CAREERS', '1;31'),
        'London, UK',
        styled(prose(filter_summary()), '2'),
        styled(prose('Dates: first publication of each job post'), '2'),
        '',
        styled(f"{total} {'role' if total == 1 else 'roles'} in the last {period}", '1;32'),
        styled(prose(f'{len(jobs)} matched / Updated {checked:%d %b %Y, %H:%M %Z}'), '2'),
        rule,
    ]
    previous = 0
    for days, matches in groups.items():
        if skip_empty and not matches:
            previous = days
            continue
        heading = 'LAST 24 HOURS' if days == 1 else f'{previous}-{days} DAYS AGO'
        count = f"{len(matches)} {'role' if len(matches) == 1 else 'roles'}"
        lines.extend(['', styled(heading, '1;36') + '  ' + styled(count, '2'), ''])
        if not matches:
            lines.append(styled('  No roles in this date range.', '2'))
        for job in matches:
            title = textwrap.fill(job['title'], width=width,
                                  initial_indent='• ', subsequent_indent='  ')
            age = f"({format_age(now - job['published'])})"
            fits = len(title.rsplit('\n', 1)[-1]) + 2 + len(age) <= width
            separator = '  ' if fits else '\n  '
            lines.append(styled(title, '1') + separator + styled(age, '33'))
            lines.append('  ' + styled(job['url'], '94'))
        previous = days
    return '\n'.join(lines)


def use_colour(stream):
    """Use terminal colours only when the output supports them."""
    if (not stream.isatty() or os.environ.get('TERM') == 'dumb'
            or 'NO_COLOR' in os.environ):
        return False
    if os.name == 'nt':
        # Enable ANSI colours in Windows consoles without installing a package.
        import ctypes
        from ctypes import wintypes
        import msvcrt

        try:
            handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
            mode = wintypes.DWORD()
            kernel = ctypes.windll.kernel32
            return bool(kernel.GetConsoleMode(handle, ctypes.byref(mode))
                        and kernel.SetConsoleMode(handle, mode.value | 0x0004))
        except (OSError, ValueError):
            return False
    return True


def run(*, last24=False, now=None, stdout=None, stderr=None, skip_empty=False):
    """Fetch and print this company's report.

    last24: Restrict results to the inclusive last 24 hours.
    skip_empty: Omit empty reports and date groups.
    now: Shared report timestamp for a batch; defaults to the current time.
    stdout, stderr: Report destinations; default to the console streams.
    """
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    if now is None:
        now = time.time()
    try:
        jobs = collect(since=now - SECONDS_PER_DAY) if last24 else collect()
    except (OSError, ValueError) as error:
        label = '\033[1;31mError\033[0m' if use_colour(stderr) else 'Error'
        print(f'{label}: Could not fetch Anthropic Careers jobs.\n{error}', file=stderr)
        return 1
    report = format_jobs(jobs, now, last24=last24, skip_empty=skip_empty,
                         colour=use_colour(stdout),
                         width=shutil.get_terminal_size(fallback=(88, 24)).columns)
    if report:
        print(report, file=stdout)
    return 0


def main(argv=None):
    """Fetch the fixed search, optionally restricted to the last 24 hours."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--last-24h', action='store_true',
                        help='Show only jobs posted in the last 24 hours; skip older date groups.')
    args = parser.parse_args(argv)
    return run(last24=args.last_24h)


if __name__ == '__main__':
    sys.exit(main())
