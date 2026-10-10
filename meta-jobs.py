#!/usr/bin/env python3
"""Fetch Meta Careers jobs and print non-overlapping date groups.

Standalone: requires only Python 3.8+ and internet access.
No packages, browser, account, configuration, or companion files are needed.
Dates use Meta's structured datePosted field on each job page.
"""

import concurrent.futures
import argparse
from datetime import datetime
from html.parser import HTMLParser
import json
import os
import re
import shutil
import sys
import textwrap
import time
import urllib.parse
import urllib.request


DAY_WINDOWS = (1, 3, 7, 14, 30)
SECONDS_PER_DAY = 86400
TEAMS = (
    'Sales & Marketing',
    'Product Management',
    'Global Operations',
    'Infrastructure',
    'Communications & Public Policy',
    'Business Development & Partnerships',
    'Legal, Finance, Facilities & Admin',
)
SEARCH_URL = 'https://www.metacareers.com/jobsearch/?' + urllib.parse.urlencode(
    [(f'teams[{i}]', team) for i, team in enumerate(TEAMS)]
    + [('offices[0]', 'London, UK'), ('roles[0]', 'Full time employment'),
       ('sort_by_new', 'true')])


class Scripts(HTMLParser):
    """Read JSON scripts and JavaScript URLs without executing JavaScript."""

    def __init__(self):
        super().__init__()
        self.kind = None
        self.parts = []
        self.data = []
        self.sources = {}

    def handle_starttag(self, tag, attrs):
        if tag != 'script':
            return
        attrs = dict(attrs)
        self.kind = attrs.get('type')
        self.parts = []
        if attrs.get('data-bootloader-hash') and attrs.get('src'):
            self.sources[attrs['data-bootloader-hash']] = attrs['src']

    def handle_data(self, data):
        if self.kind in ('application/json', 'application/ld+json'):
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script':
            if self.kind in ('application/json', 'application/ld+json'):
                self.data.append(json.loads(''.join(self.parts)))
            self.kind = None


def fetch(request):
    """Retry temporary network interruptions without requiring a browser."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode('utf-8')
        except OSError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def get_page(url):
    return fetch(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}))


def search_jobs():
    """Discover Meta's current public search request from its page assets."""
    scripts = Scripts()
    scripts.feed(get_page(SEARCH_URL))
    resources = {}
    job_resources = []
    token = None

    def walk(value):
        nonlocal token
        if isinstance(value, dict):
            resources.update(value.get('rsrcMap', {}))
            if 'CPJobSearch.react' in value:
                job_resources.extend(value['CPJobSearch.react']['r'])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            if (len(value) >= 3 and value[0] == 'LSD'
                    and isinstance(value[2], dict)):
                token = value[2].get('token')
            for child in value:
                walk(child)

    for data in scripts.data:
        walk(data)
    if not isinstance(token, str) or not token:
        raise ValueError('Meta anonymous search token could not be recognised.')
    operations = {}
    seen_urls = set()
    for resource in job_resources:
        asset = resources.get(resource, {})
        url = asset.get('src') or scripts.sources.get(resource)
        if (not url or '.js' not in url or url in seen_urls
                or urllib.parse.urlparse(url).hostname != 'static.xx.fbcdn.net'):
            continue
        seen_urls.add(url)
        source = get_page(url)
        for name, operation in re.findall(
                r'__d\("(CareersJobSearchResults(?:V2)?DataQuery)_candidate_portalRelayOperation"'
                r',.*?\b\w+\.exports\s*=\s*"(\d+)"', source, re.DOTALL):
            operations[name] = operation
        if operations:
            break
    if not operations:
        raise ValueError('Meta public search request could not be recognised.')
    name = ('CareersJobSearchResultsV2DataQuery' if 'CareersJobSearchResultsV2DataQuery'
            in operations else 'CareersJobSearchResultsDataQuery')
    variables = {'isLoggedIn': False, 'viewasUserID': None,
                 'search_input': {'teams': list(TEAMS), 'offices': ['London, UK'],
                                  'roles': ['Full time employment'],
                                  'sort_by_new': True, 'results_per_page': None}}
    body = urllib.parse.urlencode({'lsd': token, 'doc_id': operations[name],
                                  'variables': json.dumps(variables),
                                  'fb_api_req_friendly_name': name,
                                  'server_timestamps': 'true'}).encode('utf-8')
    request = urllib.request.Request('https://www.metacareers.com/api/graphql/', data=body,
                                    headers={'User-Agent': 'Mozilla/5.0', 'X-FB-LSD': token,
                                             'Content-Type': 'application/x-www-form-urlencoded'})
    source = fetch(request)
    if source.startswith('for (;;);'):
        source = source[len('for (;;);'):]
    data = json.loads(source)
    if not isinstance(data, dict) or data.get('errors'):
        raise ValueError('Meta public search returned an error.')
    field = ('job_search_with_featured_jobs_v2' if name.endswith('V2DataQuery')
             else 'job_search_with_featured_jobs')
    try:
        jobs = data['data'][field]['all_jobs']
    except (KeyError, TypeError) as error:
        raise ValueError('Meta job feed could not be recognised.') from error
    if not isinstance(jobs, list):
        raise ValueError('Meta job feed could not be recognised.')
    return jobs


def fetch_job(raw):
    """Read employment type, exact locations, and date from a public job page."""
    url = 'https://www.metacareers.com/jobs/' + raw['id'] + '/'
    scripts = Scripts()
    scripts.feed(get_page(url))
    postings = [data for data in scripts.data
                if isinstance(data, dict) and data.get('@type') == 'JobPosting']
    if len(postings) != 1:
        raise ValueError('Meta job data could not be recognised for ' + url)
    job = postings[0]
    if job.get('employmentType') != 'FULL_TIME':
        return None
    locations = job['jobLocation']
    if (not isinstance(locations, list) or len(locations) != 1
            or locations[0]['address']['addressLocality'] != 'London'
            or locations[0]['address']['addressCountry'] != 'GB'):
        return None
    title = job['title']
    if not isinstance(title, str) or not title.strip():
        raise ValueError('Missing Meta job title for ' + url)
    stamp = datetime.fromisoformat(job['datePosted'].replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Meta posting date has no timezone for ' + url)
    return {'title': title.strip(), 'url': url, 'published': stamp.timestamp()}


def collect(*, since=None):
    """Fetch all selected full-time London-only postings, newest first.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    selected = []
    seen = set()
    try:
        for raw in search_jobs():
            if (not isinstance(raw['id'], str) or not re.fullmatch(r'[0-9]+', raw['id'])
                    or not isinstance(raw['locations'], list)
                    or not isinstance(raw['teams'], list)
                    or any(not isinstance(value, str) or not value.strip()
                           for value in raw['locations'] + raw['teams'])):
                raise ValueError('Unrecognised Meta job ID, locations, or teams.')
            if (raw['locations'] == ['London, UK']
                    and any(team in TEAMS for team in raw['teams'])):
                if raw['id'] in seen:
                    raise ValueError('Duplicate jobs in the feed. Please run the script again.')
                seen.add(raw['id'])
                selected.append(raw)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [job for job in pool.map(fetch_job, selected) if job is not None]
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('Meta job data format changed.') from error
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
        jobs: Meta job records containing title, url, and published timestamp.
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
        styled('META CAREERS', '1;31'),
        'London, UK',
        styled(prose('Full-time / Teams: ' + ', '.join(TEAMS)), '2'),
        styled(prose('Dates: posting date supplied by Meta'), '2'),
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
        print(f'{label}: Could not fetch Meta Careers jobs.\n{error}', file=stderr)
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
