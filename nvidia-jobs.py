#!/usr/bin/env python3
"""Fetch NVIDIA Careers jobs and print non-overlapping date groups.

Standalone: requires only Python 3.8+ and internet access.
No packages, browser, accounts, configuration, or companion files are needed.
Dates use NVIDIA's reported posting timestamp.
Location matching follows NVIDIA's London search, including nearby and UK remote roles.
"""

import argparse
from datetime import datetime
from email.utils import parsedate_to_datetime
import json
import os
import shutil
import sys
import textwrap
import time
import urllib.error
import urllib.request


DAY_WINDOWS = (1, 3, 7, 14, 30)
SECONDS_PER_DAY = 86400


SEARCH_URL = ('https://jobs.nvidia.com/api/pcsx/search?domain=nvidia.com&'
              'location=London%2C%20UK&filter_time_type=full%20time&sort_by=timestamp')


def fetch_page(url):
    """Retry intermittent public-feed refusals without advancing pagination."""
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if attempt == 2 or error.code not in (403, 429, 500, 502, 503, 504):
                raise
            delay = 2 ** (attempt + 1)
            retry_after = error.headers.get('Retry-After', '') if error.headers else ''
            if retry_after:
                try:
                    requested_delay = int(retry_after)
                except ValueError:
                    try:
                        requested_delay = parsedate_to_datetime(retry_after).timestamp() - time.time()
                    except (ValueError, TypeError, OverflowError):
                        requested_delay = 0
                # Do not retry earlier than NVIDIA requests or overrun the runner.
                if requested_delay > 30:
                    raise
                delay = max(delay, requested_delay)
            error.close()
            time.sleep(delay)


def collect(*, since=None):
    """Fetch every page of NVIDIA's London, UK full-time search.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    jobs = []
    start = 0
    total = None
    seen = set()
    while total is None or start < total:
        payload = fetch_page(SEARCH_URL + '&start=' + str(start))
        if (not isinstance(payload, dict) or payload.get('status') != 200
                or not isinstance(payload.get('data'), dict)):
            raise ValueError('NVIDIA job feed could not be recognised.')
        data = payload['data']
        if not isinstance(data.get('positions'), list):
            raise ValueError('NVIDIA job feed could not be recognised.')
        filters = data.get('appliedFilters')
        if not isinstance(filters, dict) or filters.get('timeType') != ['full time']:
            raise ValueError('NVIDIA did not confirm the full-time filter.')
        count = data.get('count')
        if type(count) is not int or not 0 <= count <= 10000:
            raise ValueError('Unexpected NVIDIA pagination data.')
        if total is not None and count != total:
            raise ValueError('Results changed during collection. Please run the script again.')
        total = count
        if start + len(data['positions']) > total or (start < total and not data['positions']):
            raise ValueError('Incomplete NVIDIA search results. Please run the script again.')
        for raw in data['positions']:
            if not isinstance(raw, dict) or type(raw.get('id')) is not int or raw['id'] <= 0:
                raise ValueError('NVIDIA job data format changed.')
            if raw['id'] in seen:
                raise ValueError('Duplicate NVIDIA jobs. Please run the script again.')
            seen.add(raw['id'])
            if any(not isinstance(raw.get(key), list)
                   or any(not isinstance(value, str) for value in raw[key])
                   for key in ('locations', 'standardizedLocations')):
                raise ValueError('NVIDIA job locations could not be recognised.')
            if not isinstance(raw.get('name'), str) or not raw['name'].strip():
                raise ValueError('Missing NVIDIA job title.')
            if raw.get('positionUrl') != '/careers/job/' + str(raw['id']):
                raise ValueError('NVIDIA job URL could not be recognised.')
            stamp = raw.get('postedTs')
            if type(stamp) not in (int, float) or not 946684800 <= stamp <= time.time() + 86400:
                raise ValueError('NVIDIA posting date could not be recognised.')
            jobs.append({'title': raw['name'],
                         'url': 'https://jobs.nvidia.com' + raw['positionUrl'],
                         'published': stamp})
        start += len(data['positions'])
        # The search requests timestamp order; later pages are older still.
        if (since is not None and data['positions']
                and data['positions'][-1]['postedTs'] < since):
            break
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
        jobs: NVIDIA job records containing title, url, and published timestamp.
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
        styled('NVIDIA CAREERS', '1;31'),
        'London, UK',
        styled(prose('Full-time / All roles, levels and degrees / Nearby + UK remote included'), '2'),
        styled(prose('Dates: posting timestamp reported by NVIDIA'), '2'),
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
        print(f'{label}: Could not fetch NVIDIA Careers jobs.\n{error}', file=stderr)
        if isinstance(error, urllib.error.HTTPError):
            print(f'Request URL: {error.url}', file=stderr)
            error.close()
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
