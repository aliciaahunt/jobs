#!/usr/bin/env python3
"""Fetch BCG's full-time London jobs using its public career search.

Standalone: requires only Python 3.8+ and internet access.
No packages, browser, accounts, configuration, or companion files are needed.
Dates use BCG's postedDate field, which may reflect republication.
"""

import argparse
from datetime import datetime
import json
import os
import re
import shutil
import sys
import textwrap
import time
import urllib.request


SEARCH_URL = 'https://careers.bcg.com/global/en/search-results'
API_URL = 'https://careers.bcg.com/widgets'
JOB_URL = 'https://careers.bcg.com/global/en/job/'


def collect(*, since=None):
    """Fetch every page of full-time London jobs, including secondary offices.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    rows = []
    total = None
    while total is None or len(rows) < total:
        payload = {
            'lang': 'en_global', 'deviceType': 'desktop', 'country': 'global',
            'pageName': 'search-results', 'ddoKey': 'refineSearch',
            # Prefer one response: relevance ordering can shift between pages.
            'refNum': 'BCG1US', 'from': len(rows), 'size': 1000,
            'jobs': True, 'counts': True, 'clearAll': False, 'keywords': '',
            'all_fields': ['country', 'city', 'category', 'company', 'type', 'jobType'],
            'selected_fields': {'city': ['London'], 'country': ['United Kingdom'],
                                'type': ['Full-Time']},
        }
        request = urllib.request.Request(API_URL, data=json.dumps(payload).encode(),
                                         headers={'User-Agent': 'Mozilla/5.0',
                                                  'Content-Type': 'application/json',
                                                  'Referer': SEARCH_URL})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        batch = data.get('refineSearch') if isinstance(data, dict) else None
        if (not isinstance(batch, dict) or batch.get('status') != 200
                or type(batch.get('totalHits')) is not int
                or not 0 <= batch['totalHits'] <= 10000
                or not isinstance(batch.get('data'), dict)
                or not isinstance(batch['data'].get('jobs'), list)
                or type(batch.get('hits')) is not int
                or batch['hits'] != len(batch['data']['jobs'])):
            raise ValueError('BCG job search data could not be recognised.')
        if total is not None and batch['totalHits'] != total:
            raise ValueError('Search results changed. Please run the script again.')
        total = batch['totalHits']
        page = batch['data']['jobs']
        if len(rows) + len(page) > total or (not page and len(rows) < total):
            raise ValueError('Incomplete BCG search. Please run the script again.')
        rows.extend(page)

    identifiers = [raw.get('jobSeqNo') for raw in rows if isinstance(raw, dict)]
    if len(identifiers) != len(rows) or any(not isinstance(key, str) for key in identifiers):
        raise ValueError('BCG job data format changed.')
    if len(set(identifiers)) != len(rows):
        raise ValueError('Duplicate BCG listings. Please run the script again.')

    jobs = []
    for raw in rows:
        if (any(not isinstance(raw.get(key), str) or not raw[key].strip()
                for key in ('title', 'jobSeqNo', 'type', 'postedDate', 'city', 'country'))
                or not re.fullmatch(r'[A-Za-z0-9_-]+', raw['jobSeqNo'])
                or not isinstance(raw.get('multi_location', []), list)
                or any(not isinstance(location, str) or not location.strip()
                       for location in raw.get('multi_location', []))):
            raise ValueError('BCG job data format changed.')
        london = (raw['city'] == 'London' and raw['country'] == 'United Kingdom'
                  or 'London, United Kingdom' in raw.get('multi_location', []))
        if raw['type'] != 'Full-Time' or not london:
            continue
        stamp = datetime.strptime(raw['postedDate'], '%Y-%m-%dT%H:%M:%S.%f%z')
        jobs.append({'title': raw['title'].strip(), 'url': JOB_URL + raw['jobSeqNo'],
                     'published': stamp.timestamp()})
    if since is not None:
        jobs = [job for job in jobs if job['published'] is not None
                and since <= job['published'] <= since + SECONDS_PER_DAY]
    return sorted(jobs, key=lambda job: job['published'], reverse=True)


DAY_WINDOWS = (1, 3, 7, 14, 30)
SECONDS_PER_DAY = 86400


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
        jobs: BCG job records containing title, url, and published timestamp.
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
        styled('BCG CAREERS', '1;31'),
        'London, UK',
        styled(prose("Full-time / All roles, levels and degrees"), '2'),
        styled(prose('Dates: BCG posted date, which may include republication'), '2'),
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
        print(f'{label}: Could not fetch BCG Careers jobs.\n{error}', file=stderr)
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
