#!/usr/bin/env python3
"""Fetch PA Consulting jobs and print non-overlapping date groups.

Standalone: requires only Python 3.8+ and internet access.
Dates use SmartRecruiters' release timestamp, which may include republication.
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


SEARCH_URL = 'https://api.smartrecruiters.com/v1/companies/PAConsulting/postings'


def collect(*, since=None):
    """Fetch public full-time London, UK postings at every level.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    raw_jobs = []
    offset = 0
    total = None
    while True:
        request = urllib.request.Request(SEARCH_URL + f'?limit=100&offset={offset}',
                                         headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        if (not isinstance(data, dict) or not isinstance(data.get('content'), list)
                or any(type(data.get(key)) is not int for key in ('offset', 'limit', 'totalFound'))
                or data['offset'] != offset or not 1 <= data['limit'] <= 100
                or not 0 <= data['totalFound'] <= 10000):
            raise ValueError('PA Consulting job feed or pagination could not be recognised.')
        if total is None:
            total = data['totalFound']
        if (data['totalFound'] != total
                or len(data['content']) != min(data['limit'], total - offset)):
            raise ValueError('Incomplete or changing job feed. Please run the script again.')
        raw_jobs.extend(data['content'])
        offset += data['limit']
        if offset >= data['totalFound']:
            break
    jobs = []
    seen = set()
    for raw in raw_jobs:
        try:
            if (any(not isinstance(raw[key], str) or not raw[key].strip()
                    for key in ('id', 'name', 'visibility'))
                    or not raw['id'].isdigit()
                    or raw['company']['identifier'] != 'PAConsulting'):
                raise ValueError('Invalid PA Consulting job identity or title.')
            if raw['id'] in seen:
                raise ValueError('Duplicate jobs in the feed. Please run the script again.')
            seen.add(raw['id'])
            city = raw['location']['city'].strip().casefold()
            country = raw['location']['country'].strip().casefold()
            employment = raw['typeOfEmployment']['label'].strip().casefold()
            if (city != 'london' or country != 'gb' or employment != 'full-time'
                    or raw['visibility'] != 'PUBLIC'):
                continue
            stamp = datetime.fromisoformat(raw['releasedDate'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('Publication date has no timezone.')
            jobs.append({'title': raw['name'].strip(),
                         'url': 'https://jobs.smartrecruiters.com/PAConsulting/' + raw['id'],
                         'published': stamp.timestamp()})
        except (KeyError, TypeError, AttributeError, OverflowError) as error:
            raise ValueError('PA Consulting job data format changed.') from error
    if since is not None:
        jobs = [job for job in jobs if job['published'] is not None
                and since <= job['published'] <= since + SECONDS_PER_DAY]
    return sorted(jobs, key=lambda job: job['published'], reverse=True)


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
        jobs: PA Consulting job records containing title, url, and published timestamp.
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
        styled('PA CONSULTING CAREERS', '1;31'),
        'London, UK',
        styled(prose("Full-time / All roles, levels and degrees"), '2'),
        styled(prose('Dates: publication, which may include republication'), '2'),
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
        print(f'{label}: Could not fetch PA Consulting Careers jobs.\n{error}', file=stderr)
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
