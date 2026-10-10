#!/usr/bin/env python3
"""Fetch McKinsey's full-time London jobs.

Requires only Python 3.8+ and internet access: no login or browser.
Dates use McKinsey's job-order creation date, at midnight UTC; this is
not a verified publication date and can be older than a republication.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import re
import shutil
import sys
import textwrap
import time
import urllib.parse
import urllib.request


SEARCH_URL = 'https://www.mckinsey.com/careers/search-jobs?cities=London'
API_URL = 'https://gateway.mckinsey.com/apigw-x0cceuow60/v1/api/jobs/'


def fetch(endpoint, **params):
    """Read the unauthenticated API used by McKinsey's career page."""
    url = API_URL + endpoint + '?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0', 'Referer': SEARCH_URL,
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    if (not isinstance(data, dict) or data.get('httpStatus') != 'OK'
            or not isinstance(data.get('docs'), list)
            or type(data.get('numFound')) is not int
            or not 0 <= data['numFound'] <= 10000):
        raise ValueError('McKinsey job data could not be recognised.')
    for row in data['docs']:
        if (not isinstance(row, dict)
                or any(not isinstance(row.get(key), str) or not row[key].strip()
                       for key in ('jobID', 'title', 'friendlyURL'))
                or not row['jobID'].isdigit()
                or not re.fullmatch(r'[A-Za-z0-9_-]+', row['friendlyURL'])):
            raise ValueError('McKinsey job data could not be recognised.')
    return data


def collect(*, since=None):
    """Return active full-time London, UK jobs, newest creation date first.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    data = fetch('search', pageSize=100, start=1, cities='London',
                 countries='United Kingdom', lang='en')
    rows = list(data['docs'])
    for page in range(2, (data['numFound'] + 99) // 100 + 1):
        batch = fetch('search', pageSize=100, start=page, cities='London',
                      countries='United Kingdom', lang='en')
        if batch['numFound'] != data['numFound']:
            raise ValueError('Search results changed. Please run the script again.')
        rows.extend(batch['docs'])
    if len(rows) != data['numFound']:
        raise ValueError('Incomplete McKinsey search. Please run the script again.')
    if (len({row['jobID'] for row in rows}) != len(rows)
            or len({row['friendlyURL'] for row in rows}) != len(rows)):
        raise ValueError('Duplicate McKinsey listings. Please run the script again.')
    jobs = []
    for row in rows:
        details = fetch('details', friendlyUrl=row['friendlyURL'])
        if details['numFound'] != 1 or len(details['docs']) != 1:
            raise ValueError('Missing McKinsey job details. Please run the script again.')
        raw = details['docs'][0]
        if any(raw[key] != row[key] for key in ('jobID', 'title', 'friendlyURL')):
            raise ValueError('McKinsey job changed. Please run the script again.')
        if (any(not isinstance(raw.get(key), str) or not raw[key].strip()
                for key in ('status', 'typeOfRole'))
                or not isinstance(raw.get('hiringLocations'), list)
                or not raw['hiringLocations']
                or any(not isinstance(location, str) or not location.strip()
                       for location in raw['hiringLocations'])):
            raise ValueError('McKinsey job details could not be recognised.')
        if (raw['status'] != 'Active' or raw['typeOfRole'] != 'Full-time'
                or not any(location.rsplit('/', 1)[-1] == 'London (United Kingdom)'
                           for location in raw['hiringLocations'])):
            continue
        date = raw.get('jobOrderCreatedDate')
        if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            raise ValueError('Missing or invalid McKinsey job-order creation date.')
        stamp = datetime.strptime(date, '%Y-%m-%d').replace(
            tzinfo=timezone.utc)
        jobs.append({'title': raw['title'],
                     'url': 'https://www.mckinsey.com/careers/search-jobs/jobs/'
                            + raw['friendlyURL'],
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
        jobs: McKinsey job records containing title, url, and published timestamp.
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
        styled('MCKINSEY CAREERS', '1;31'),
        'London, UK',
        styled(prose("Full-time / All roles, levels and degrees"), '2'),
        styled(prose('Dates: job-order creation date (UTC), not verified publication'), '2'),
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
        print(f'{label}: Could not fetch McKinsey Careers jobs.\n{error}', file=stderr)
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
