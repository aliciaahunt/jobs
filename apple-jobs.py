#!/usr/bin/env python3
"""Fetch Apple Careers jobs and print non-overlapping date groups.

Standalone: requires only Python 3.8+ and internet access.
No packages, browser, accounts, configuration, or companion files are needed.
Location matching follows Apple's London search, including London-area and
UK-wide retail postings returned by that search.
Apple has no full-time search flag: include postings listing 35+ weekly hours.
Retail postings may also offer part-time work. Dates are Apple's posting dates.
"""

import argparse
from datetime import datetime
import json
import os
import shutil
import sys
import time
import re
import textwrap
import urllib.request


SEARCH_URL = 'https://jobs.apple.com/en-gb/search?location=london-LND&sort=newest'
MIN_WEEKLY_HOURS = 35
DAY_WINDOWS = (1, 3, 7, 14, 30)
SECONDS_PER_DAY = 86400


class SearchChanged(ValueError):
    """Apple returned pages that do not form a complete, consistent search."""


def fetch_page(page):
    """Read Apple's public search data without executing JavaScript."""
    request = urllib.request.Request(SEARCH_URL + '&page=' + str(page),
                                     headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(2):
        with urllib.request.urlopen(request, timeout=30) as response:
            source = response.read().decode('utf-8')
        match = re.search(r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\(', source)
        if match is None:
            raise ValueError('Apple search data could not be recognised.')
        try:
            serialized, _ = json.JSONDecoder().raw_decode(source[match.end():].lstrip())
            data = json.loads(serialized)['loaderData']['search']
            if not isinstance(data, dict) or not isinstance(data.get('searchResults'), list):
                raise ValueError('Apple search data could not be recognised.')
            locations = data['filters']['locations']
            if not isinstance(locations, list) or not any(
                    location['id'] == 'postLocation-LND' for location in locations):
                raise ValueError('Apple did not confirm the London location filter.')
            if type(data.get('page')) is not int or data['page'] != page:
                raise ValueError('Apple returned an unexpected search page.')
            # Apple occasionally returns a transient empty search; confirm it once.
            if attempt == 0 and data.get('totalRecords') == 0 and not data['searchResults']:
                continue
            return data
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError('Apple search data format changed.') from error


def _collect_once(*, since=None):
    """Fetch and validate one complete London search."""
    jobs = []
    page = 1
    fetched = 0
    total = None
    seen = set()
    while total is None or fetched < total:
        data = fetch_page(page)
        count = data.get('totalRecords')
        if type(count) is not int or not 0 <= count <= 10000:
            raise ValueError('Unexpected Apple pagination data.')
        if total is not None and count != total:
            raise SearchChanged('Results changed during collection.')
        total = count
        if (fetched + len(data['searchResults']) > total
                or (fetched < total and not data['searchResults'])):
            raise SearchChanged('Incomplete Apple search results.')
        page_is_old = bool(data['searchResults'])
        for raw in data['searchResults']:
            if (not isinstance(raw, dict) or not isinstance(raw.get('reqId'), str)
                    or not re.fullmatch(r'(?:PIPE-)?[0-9]+(?:-[0-9]+)?', raw['reqId'])):
                raise ValueError('Apple job identifier could not be recognised.')
            if raw['reqId'] in seen:
                raise SearchChanged('Duplicate Apple jobs.')
            seen.add(raw['reqId'])
            hours = raw.get('standardWeeklyHours')
            if type(hours) not in (int, float) or not 0 <= hours <= 168:
                raise ValueError('Apple weekly hours could not be recognised.')
            if hours < MIN_WEEKLY_HOURS and since is None:
                continue
            if not isinstance(raw.get('postDateInGMT'), str):
                raise ValueError('Missing Apple posting date.')
            # Python 3.8/3.9 accept microseconds; Apple sometimes sends nanoseconds.
            date = re.sub(r'(\.\d{6})\d+', r'\1', raw['postDateInGMT'])
            stamp = datetime.fromisoformat(date.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('Apple posting date has no timezone.')
            if since is not None:
                page_is_old = stamp.timestamp() < since
            if hours < MIN_WEEKLY_HOURS:
                continue
            if (not isinstance(raw.get('postingTitle'), str)
                    or not raw['postingTitle'].strip()):
                raise ValueError('Missing Apple job title.')
            slug = raw.get('transformedPostingTitle')
            team = raw.get('team')
            if (not isinstance(slug, str) or not re.fullmatch(r'[\w-]+', slug)
                    or not isinstance(team, dict) or not isinstance(team.get('teamCode'), str)
                    or not re.fullmatch(r'[A-Z0-9]+', team['teamCode'])):
                raise ValueError('Apple job URL could not be recognised.')
            number = raw['reqId'][5:] if raw['reqId'].startswith('PIPE-') else raw['reqId']
            url = ('https://jobs.apple.com/en-gb/details/' + number + '/' +
                   slug + '?team=' + team['teamCode'])
            jobs.append({'title': raw['postingTitle'], 'url': url,
                         'published': stamp.timestamp()})
        # Search is ordered newest first, including jobs excluded by working hours.
        if since is not None and page_is_old:
            break
        fetched += len(data['searchResults'])
        page += 1
    if since is not None:
        jobs = [job for job in jobs if job['published'] is not None
                and since <= job['published'] <= since + SECONDS_PER_DAY]
    return sorted(jobs, key=lambda job: job['published'], reverse=True)


def collect(*, since=None):
    """Return a complete search, restarting automatically if Apple's pages change.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    for attempt in range(3):
        try:
            return _collect_once(since=since)
        except SearchChanged:
            if attempt == 2:
                raise SearchChanged('Apple returned inconsistent search pages after 3 attempts.')


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
        jobs: Apple job records containing title, url, and published timestamp.
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
        styled('APPLE CAREERS', '1;31'),
        'London, UK',
        styled(prose("Full-time (35+ listed weekly hours) / All roles, levels and degrees"), '2'),
        styled(prose('Dates: Apple posting date / Retail roles may also offer part-time'), '2'),
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
        print(f'{label}: Could not fetch Apple Careers jobs.\n{error}', file=stderr)
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
