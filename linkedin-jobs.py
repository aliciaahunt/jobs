#!/usr/bin/env python3
"""Fetch LinkedIn's own vacancies with the same UI as the careers scripts.

Standalone: requires only Python 3.8+ and internet access. No login or browser.
Guest search is limited: results are not an exhaustive list of all vacancies.
"""

import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import os
import re
import shutil
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request


DAY_WINDOWS = (1, 3, 7, 14, 30)
SECONDS_PER_DAY = 86400


SEARCH_URL = ('https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?'
              + urllib.parse.urlencode({'keywords': '',
                  'location': 'London, England, United Kingdom',
                  'geoId': '102257491', 'distance': '0', 'f_JT': 'F',
                  'f_C': '1337',  # LinkedIn's employer ID.
                  'f_TPR': 'r2592000', 'sortBy': 'DD'}))
GUEST_LIMIT = 1000


class JobCards(HTMLParser):
    """Read the public search cards without executing JavaScript."""

    def __init__(self):
        super().__init__()
        self.jobs = []
        self.current = None
        self.depth = 0
        self.capture = None

    def handle_starttag(self, tag, attrs):
        if tag in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                   'link', 'meta', 'param', 'source', 'track', 'wbr'):
            return
        attrs = dict(attrs)
        classes = (attrs.get('class') or '').split()
        if tag == 'div' and 'job-search-card' in classes:
            if self.current is not None:
                raise ValueError('LinkedIn returned an incomplete job card.')
            self.current = {'id': (attrs.get('data-entity-urn') or '').rsplit(':', 1)[-1]}
            self.depth = 0
        if self.current is None:
            return
        self.depth += 1
        if tag == 'a' and 'base-card__full-link' in classes:
            self.current['url'] = attrs.get('href')
        if tag == 'a' and self.capture and self.capture[0] == 'company':
            self.current['company_url'] = attrs.get('href')
        fields = {'base-search-card__title': 'title',
                  'base-search-card__subtitle': 'company',
                  'job-search-card__location': 'location'}
        field = next((field for css, field in fields.items() if css in classes), None)
        if tag == 'time':
            self.current['date'] = attrs.get('datetime')
            field = 'age'
        if field:
            self.current[field] = ''
            self.capture = (field, self.depth)

    def handle_data(self, data):
        if self.current is not None and self.capture:
            self.current[self.capture[0]] += data

    def handle_endtag(self, tag):
        if self.current is None:
            return
        if self.capture and self.capture[1] == self.depth:
            self.capture = None
        self.depth -= 1
        if self.depth == 0:
            self.jobs.append(self.current)
            self.current = None


def collect(*, since=None):
    """Collect LinkedIn's own full-time London vacancies from public search.

    Includes every title and experience level at LinkedIn. Public search may
    omit vacancies or terminate before the 1,000-result pagination ceiling.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    jobs = {}
    seen = set()
    now = since + SECONDS_PER_DAY if since is not None else time.time()
    search_url = SEARCH_URL.replace('f_TPR=r2592000', 'f_TPR=r86400') if since is not None else SEARCH_URL
    start = 0
    while start < GUEST_LIMIT:
        request = urllib.request.Request(search_url + '&start=' + str(start),
                                        headers={'User-Agent': 'Mozilla/5.0',
                                                 'Accept-Language': 'en-GB,en;q=0.9'})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    source = response.read().decode('utf-8')
                break
            except OSError as error:
                if attempt == 3 and isinstance(error, urllib.error.HTTPError) and error.code == 429:
                    raise ValueError('LinkedIn temporarily limited guest requests. '
                                     'Please run the script again later.') from error
                if (attempt == 3 or (isinstance(error, urllib.error.HTTPError)
                        and error.code not in (429, 500, 502, 503, 504))):
                    raise
                delay = 5 * (attempt + 1)
                if isinstance(error, urllib.error.HTTPError) and error.headers:
                    retry_after = error.headers.get('Retry-After', '')
                    if retry_after.isdigit():
                        delay = max(delay, min(int(retry_after), 30))
                time.sleep(delay)
        parser = JobCards()
        parser.feed(source)
        parser.close()
        if parser.current is not None:
            raise ValueError('LinkedIn returned an incomplete job card.')
        if not parser.jobs:
            remainder = re.sub(r'<!DOCTYPE\s+html\s*>|<!--[\s\S]*?-->', '', source,
                               flags=re.IGNORECASE).strip()
            if remainder or not source.strip():
                raise ValueError('LinkedIn guest search could not be recognised; access may be blocked.')
            break
        page_ids = {raw['id'] for raw in parser.jobs}
        if not page_ids - seen:
            raise ValueError('LinkedIn repeated a results page. Please run the script again later.')
        seen.update(page_ids)
        for raw in parser.jobs:
            if any(not isinstance(raw.get(key), str) or not raw[key].strip()
                   for key in ('id', 'title', 'location', 'date', 'age', 'url',
                               'company', 'company_url')):
                raise ValueError('LinkedIn job data format changed or required fields are missing.')
            if not re.fullmatch(r'[0-9]+', raw['id']):
                raise ValueError('LinkedIn returned an invalid job ID.')
            url = urllib.parse.urlparse(raw['url'])
            if (url.scheme != 'https' or
                    (url.hostname != 'linkedin.com' and not (url.hostname or '').endswith('.linkedin.com'))
                    or not re.fullmatch(r'/jobs/view/(?:[^/]+-)?' + raw['id'], url.path)):
                raise ValueError('LinkedIn returned an invalid job URL.')
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw['date']):
                raise ValueError('LinkedIn returned an invalid posting date.')
            stamp = datetime.fromisoformat(raw['date']).replace(tzinfo=timezone.utc)
            employer = urllib.parse.urlparse(raw['company_url'])
            if (employer.scheme != 'https' or
                    (employer.hostname != 'linkedin.com' and
                     not (employer.hostname or '').endswith('.linkedin.com')) or
                    employer.path.rstrip('/') not in ('/company/linkedin', '/company/1337') or
                    ' '.join(raw['company'].split()).casefold() != 'linkedin'):
                continue
            location = ' '.join(raw['location'].split())
            if location not in ('London, England, United Kingdom',
                                 'Greater London, England, United Kingdom',
                                 'London Area, United Kingdom', 'London, United Kingdom'):
                continue
            published = stamp.timestamp()
            recent = re.fullmatch(r'(\d+)\s+(minute|hour)s?\s+ago', raw['age'].strip())
            if recent:
                unit = 60 if recent[2] == 'minute' else 3600
                published = now - int(recent[1]) * unit
            jobs[raw['id']] = {'title': ' '.join(raw['title'].split()),
                              'url': 'https://www.linkedin.com/jobs/view/' + raw['id'],
                              'published': published}
        start += len(parser.jobs)
        time.sleep(0.5)
    jobs = list(jobs.values())
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
        jobs: LinkedIn job records containing title, url, and published timestamp.
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
        styled('LINKEDIN CAREERS', '1;31'),
        'London, UK',
        styled(prose("Full-time / All roles at LinkedIn"), '2'),
        styled(prose('Dates: LinkedIn posting dates; times may be approximate'), '2'),
        styled(prose('Public results only; LinkedIn may limit or omit matches'), '2'),
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
        print(f'{label}: Could not fetch LinkedIn jobs.\n{error}', file=stderr)
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
