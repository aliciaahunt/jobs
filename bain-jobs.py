#!/usr/bin/env python3
"""Fetch Bain's full-time London roles through its public careers site.

Standalone: requires only Python 3.8+ and internet access.
No packages, browser, accounts, configuration, or companion files are needed.
Dates use Bain's datePosted, at midnight UTC; republication is possible.
Roles with no posting date remain visible in a separate undated group.
"""

import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import html
import json
import os
import shutil
import sys
import time
import textwrap
import re
import urllib.parse
import urllib.request


SEARCH_URL = 'https://www.bain.com/careers/find-a-role/'
API_URL = 'https://www.bain.com/en/api/jobsearch/keyword/get'
# London office; every full-time employment category, with all work areas/teams.
FILTERS = 'offices(231)|employmenttype(E00,E10,E30,E60)'
FULL_TIME_TYPES = ('Permanent Full-Time', 'Temporary Full-Time',
                   'Contract to Permanent (Full-Time)', 'Intern (Full-Time)')


class StructuredData(HTMLParser):
    """Read job metadata without executing page JavaScript."""

    def __init__(self):
        super().__init__()
        self.active = False
        self.current = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = dict(attrs).get('type') == 'application/ld+json'
            self.current = []

    def handle_data(self, data):
        if self.active:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.scripts.append(''.join(self.current))
            self.active = False


def collect(*, since=None):
    """Return every full-time London job, dated jobs newest first.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    rows = []
    total = None
    page = 0
    while total is None or len(rows) < total:
        url = API_URL + '?' + urllib.parse.urlencode(
            {'start': page, 'results': 100, 'filters': FILTERS, 'searchValue': ''})
        request = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': SEARCH_URL,
            'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        if (not isinstance(data, dict) or not isinstance(data.get('results'), list)
                or type(data.get('totalResults')) is not int
                or not 0 <= data['totalResults'] <= 10000):
            raise ValueError('Bain job search data could not be recognised.')
        if total is not None and data['totalResults'] != total:
            raise ValueError('Search results changed. Please run the script again.')
        total = data['totalResults']
        if (len(rows) + len(data['results']) > total
                or not data['results'] and len(rows) < total):
            raise ValueError('Incomplete Bain search. Please run the script again.')
        rows.extend(data['results'])
        page += 1
    seen = set()
    for row in rows:
        if (not isinstance(row, dict)
                or any(not isinstance(row.get(key), str) or not row[key].strip()
                       for key in ('JobId', 'JobTitle', 'Link', 'EmployeeType'))
                or not re.fullmatch(r'[0-9]+', row['JobId'])
                or not isinstance(row.get('Location'), list) or not row['Location']
                or any(not isinstance(location, str) or not location.strip()
                       for location in row['Location'])):
            raise ValueError('Bain job data format changed.')
        link = urllib.parse.urlparse(urllib.parse.urljoin(SEARCH_URL, row['Link']))
        if (link.scheme != 'https' or link.netloc != 'www.bain.com'
                or not link.path.startswith('/careers/') or link.fragment):
            raise ValueError('Unrecognised Bain job URL.')
        if row['JobId'] in seen:
            raise ValueError('Duplicate Bain listings. Please run the script again.')
        seen.add(row['JobId'])
    jobs = []
    for row in rows:
        if (row['EmployeeType'] not in FULL_TIME_TYPES
                or 'London' not in [location.strip() for location in row['Location']]):
            continue
        url = urllib.parse.urljoin(SEARCH_URL, row['Link'])
        request = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': SEARCH_URL})
        with urllib.request.urlopen(request, timeout=30) as response:
            source = response.read().decode('utf-8')
        parser = StructuredData()
        parser.feed(source)
        postings = []
        for script in parser.scripts:
            # Bain's internship descriptions include literal newlines in strings.
            raw = json.loads(script, strict=False)
            if isinstance(raw, dict) and raw.get('@type') == 'JobPosting':
                postings.append(raw)
        if len(postings) != 1:
            raise ValueError('Bain job details could not be recognised.')
        raw = postings[0]
        if (not isinstance(raw.get('title'), str)
                or html.unescape(raw['title']) != row['JobTitle']
                or raw.get('employmentType') != row['EmployeeType']):
            raise ValueError('Bain job changed. Please run the script again.')
        date = raw.get('datePosted')
        if (not isinstance(date, str)
                or date and not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', date)):
            raise ValueError('Missing or invalid Bain posting date.')
        stamp = (datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                 .timestamp() if date else None)
        jobs.append({'title': row['JobTitle'], 'url': url, 'published': stamp})
    if since is not None:
        jobs = [job for job in jobs if job['published'] is not None
                and since <= job['published'] <= since + SECONDS_PER_DAY]
    return sorted(jobs, key=lambda job: (job['published'] is not None,
                                        job['published'] or 0), reverse=True)


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
        jobs: Bain job records containing title, url, and published timestamp.
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
    undated = [job for job in jobs if job['published'] is None]
    dated = [job for job in jobs if job['published'] is not None]
    for job in sorted(dated, key=lambda job: job['published'], reverse=True):
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
    if skip_empty and total == 0 and not undated:
        return ''
    checked = datetime.fromtimestamp(now).astimezone()
    rule = styled('-' * width, '2')
    period = '24 hours' if last24 else '30 days'
    lines = [
        styled('BAIN CAREERS', '1;31'),
        'London, UK',
        styled(prose("Full-time / All roles, levels and degrees"), '2'),
        styled(prose('Dates: Bain posted date (UTC); ' +
                     ('undated roles excluded' if last24 else 'undated roles shown separately')), '2'),
        '',
        *([styled(f"{total} {'role' if total == 1 else 'roles'} in the last {period}", '1;32')]
          if total or not skip_empty else []),
        styled(prose(f'{len(jobs)} matched / Updated {checked:%d %b %Y, %H:%M %Z}'), '2'),
        rule,
    ]
    if undated:
        groups[None] = undated
    previous = 0
    for days, matches in groups.items():
        if skip_empty and not matches:
            previous = days
            continue
        heading = ('DATE UNAVAILABLE' if days is None else
                   'LAST 24 HOURS' if days == 1 else f'{previous}-{days} DAYS AGO')
        count = f"{len(matches)} {'role' if len(matches) == 1 else 'roles'}"
        lines.extend(['', styled(heading, '1;36') + '  ' + styled(count, '2'), ''])
        if not matches:
            lines.append(styled('  No roles in this date range.', '2'))
        for job in matches:
            title = textwrap.fill(job['title'], width=width,
                                  initial_indent='• ', subsequent_indent='  ')
            age = ('(Date unavailable)' if job['published'] is None else
                   f"({format_age(now - job['published'])})")
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
        print(f'{label}: Could not fetch Bain Careers jobs.\n{error}', file=stderr)
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
