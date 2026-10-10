#!/usr/bin/env python3
"""Fetch Google Careers jobs and print non-overlapping date groups.

Standalone: requires only Python 3.8+ and internet access.
No packages, browser, accounts, configuration, or companion files are needed.

Dates use each job's earliest embedded timestamp, not a verified opening date.
"""

import concurrent.futures
import argparse
from datetime import datetime, timezone
import html
from html.parser import HTMLParser
import json
import os
import re
import shutil
import sys
import textwrap
import time
import urllib.request


SEARCH_URL = ('https://www.google.com/about/careers/applications/jobs/results?'
              'location=London%2C%20UK&target_level=MID&target_level=EARLY&'
              'degree=BACHELORS&degree=MASTERS&employment_type=FULL_TIME&sort_by=date')


DAY_WINDOWS = (1, 3, 7, 14, 30)
SECONDS_PER_DAY = 86400


class Scripts(HTMLParser):
    """Read embedded JSON-bearing scripts without executing page JavaScript."""

    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def parse_page(source):
    """Extract jobs and pagination from a Google Careers search response."""
    scripts = Scripts()
    scripts.feed(source)
    payload = None
    for script in scripts.parts:
        if not script.startswith('AF_initDataCallback('):
            continue
        match = re.search(r'\bdata:', script)
        if not match:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(script[match.end():].lstrip())
        except json.JSONDecodeError:
            continue
        if (isinstance(data, list) and len(data) == 4 and
                isinstance(data[0], (list, type(None))) and
                isinstance(data[2], int) and isinstance(data[3], int)):
            payload = data
            break
    if payload is None:
        raise ValueError('Google search data could not be recognised.')
    raw_jobs, _, total, page_size = payload
    jobs = []
    for raw in raw_jobs or []:
        if (not isinstance(raw, list) or len(raw) < 21 or
                not isinstance(raw[0], str) or not raw[0].isdigit()):
            raise ValueError('Google job data format changed.')
        stamps = []
        for index in (12, 13, 14):
            value = raw[index]
            if (not isinstance(value, list) or not value or
                    not isinstance(value[0], (int, float)) or
                    not 946684800 <= value[0] <= time.time() + 86400):
                raise ValueError(f'Unrecognised timestamp for job {raw[0]}.')
            stamps.append(value[0])
        earliest = min(stamps)
        # This name is deliberately literal: the three source fields have no public schema.
        jobs.append({'id': raw[0], 'title': raw[1], 'company': raw[7],
                     'locations': [loc[0] for loc in raw[9]],
                     'level': {1: 'Early', 2: 'Mid', 3: 'Advanced', 4: 'Director+', 5: 'Intern / Apprentice'}.get(raw[20], 'Unknown'),
                     'earliest': earliest, 'earliest_date': iso(earliest),
                     'source_timestamps': stamps, 'later_date': iso(max(stamps)),
                     'url': f'https://www.google.com/about/careers/applications/jobs/results/{raw[0]}',
                     'qualifications': re.sub('<[^>]+>', ' ', html.unescape(raw[19][1])).strip()})
    return jobs, total, page_size


def select_jobs(jobs, start, end):
    return [job for job in jobs if start <= job['earliest'] <= end]


def fetch_page(page):
    url = SEARCH_URL + '&page=' + str(page)
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return parse_page(response.read().decode('utf-8'))


def collect(*, since=None):
    """Fetch every search page and reject incomplete or duplicate results.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    first, total, size = fetch_page(1)
    if size <= 0 or total > 10000:
        raise ValueError('Unexpected pagination data.')
    pages = range(2, (total + size - 1) // size + 1)
    jobs = list(first)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for batch, count, _ in pool.map(fetch_page, pages):
            if count != total:
                raise ValueError('Results changed during collection. Please run the script again.')
            jobs.extend(batch)
    if len(jobs) != total or len({job['id'] for job in jobs}) != total:
        raise ValueError('Incomplete or changing search results. Please run the script again.')
    if since is not None:
        jobs = [job for job in jobs if job['earliest'] is not None
                and since <= job['earliest'] <= since + SECONDS_PER_DAY]
    return sorted(jobs, key=lambda job: job['earliest'], reverse=True)


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
        jobs: Google job records containing title, url, and earliest timestamp.
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
        jobs = [job for job in jobs if job['earliest'] is not None
                and 0 <= now - job['earliest'] <= SECONDS_PER_DAY]
    groups = {days: [] for days in windows}
    for job in sorted(jobs, key=lambda job: job['earliest'], reverse=True):
        age = now - job['earliest']
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
        styled('GOOGLE CAREERS', '1;31'),
        'London, UK',
        styled(prose("Early + Mid / Bachelor's + Master's / Full-time"), '2'),
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
            age = f"({format_age(now - job['earliest'])})"
            fits = len(title.rsplit('\n', 1)[-1]) + 2 + len(age) <= width
            separator = '  ' if fits else '\n  '
            lines.append(styled(title, '1') + separator + styled(age, '33'))
            lines.append('  ' + styled(job['url'], '94'))
        previous = days
    return '\n'.join(lines)


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
        print(f'{label}: Could not fetch Google Careers jobs.\n{error}', file=stderr)
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
