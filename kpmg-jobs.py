#!/usr/bin/env python3
"""Fetch KPMG's full-time, experienced Advisory vacancies in London, UK.

Standalone: requires only Python 3.8+ and internet access.
No packages, browser, accounts, configuration, or companion files are needed.
Dates come from KPMG's public experienced-professional recruitment portal.
The main careers site's datePosted is generated on each request and is ignored.
"""

import concurrent.futures
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import os
import re
import shutil
import sys
import textwrap
import time
import urllib.parse
import urllib.request


CAREERS_URL = 'https://www.kpmgcareers.co.uk'
PORTAL_URL = 'https://experienced.kpmgcareers.co.uk'
SEARCH_URL = CAREERS_URL + '/search/vacancies/?' + urllib.parse.urlencode({
    'capabilities[0]': 'Advisory', 'intakeType': 'Experienced',
    'officeLocations[0]': 'London', 'employmentTypes[0]': 'Full Time',
})
DAY_WINDOWS = (1, 3, 7, 14, 30)
SECONDS_PER_DAY = 86400


class Element:
    """Read-only HTML element with descendant text and attribute selectors."""

    def __init__(self, tag='', attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parts = []

    def text(self):
        return ''.join(part.text() if isinstance(part, Element) else part
                       for part in self.parts)

    def find(self, tag=None, class_name=None, **attrs):
        for part in self.parts:
            if not isinstance(part, Element):
                continue
            if ((tag is None or part.tag == tag)
                    and (class_name is None or class_name in
                         part.attrs.get('class', '').split())
                    and all(part.attrs.get(key) == value for key, value in attrs.items())):
                yield part
            yield from part.find(tag, class_name, **attrs)


class Page(HTMLParser):
    """Parse server-rendered HTML without running JavaScript."""

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Element()
        self.stack = [self.root]
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs)
        self.stack[-1].parts.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                       'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].parts.append(Element(tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].parts.append(data)


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return Page(response.read().decode('utf-8')).root


def search_page(page):
    document = fetch(SEARCH_URL + '&page=' + str(page))
    if next(document.find(id='vacancy-results-page'), None) is None:
        raise ValueError('KPMG vacancy search could not be recognised.')
    try:
        descriptions = {}
        for node in document.find(class_name='vacancy-description'):
            descriptions[node.attrs['data-vacancy-id']] = dict(
                field.text().strip().split(':', 1)
                for field in node.find(class_name='vacancy-details-fields'))
        jobs = []
        seen = set()
        for node in document.find(class_name='vacancy-result'):
            req = node.attrs['data-vacancy-id']
            if req in seen:
                raise ValueError('Duplicate KPMG vacancies. Please run the script again.')
            seen.add(req)
            fields = descriptions[req]
            if (fields['Employment type'].strip() != 'Full Time'
                    or fields['Capability'].strip() != 'Advisory'):
                continue
            title = next(node.find('h3')).text().strip()
            link = next(node.find('a', 'view-job-description')).attrs['href']
            if not req.isdigit() or not title or not link.startswith('/Vacancies/'):
                raise ValueError('Missing KPMG job title, requisition ID, or URL.')
            jobs.append({'id': req, 'title': title,
                         'url': urllib.parse.urljoin(CAREERS_URL, link)})
        pages = max([1] + [int(node.attrs['data-page'])
                           for node in document.find('a', 'page-link')])
        if pages > 1000:
            raise ValueError('Unexpected KPMG search pagination.')
    except (KeyError, StopIteration, TypeError) as error:
        raise ValueError('KPMG vacancy search format changed.') from error
    return jobs, pages


def portal_page(offset):
    document = fetch(PORTAL_URL + '/search?startrow=' + str(offset))
    try:
        table = next(document.find('table', id='searchresults'))
        total = int(re.search(r'\bof (\d+)\s*$', table.attrs['aria-label'])[1])
        if not 0 <= total <= 10000:
            raise ValueError('Unexpected KPMG portal pagination.')
        rows = {urllib.parse.urljoin(PORTAL_URL, node.attrs['href']): node.text().strip()
                for node in table.find('a', 'jobTitle-link')}
    except (KeyError, StopIteration, TypeError) as error:
        raise ValueError('KPMG recruitment portal could not be recognised.') from error
    return rows, total


def posting_date(url):
    document = fetch(url)
    scripts = '\n'.join(node.text() for node in document.find('script'))
    req = re.search(r'"internalId"\s*:\s*"(\d+)-en_GB"', scripts)
    meta = next(document.find('meta', itemprop='datePosted'), None)
    if req is None or meta is None or not meta.attrs.get('content'):
        raise ValueError('KPMG portal job has no requisition ID or posting date.')
    value = meta.attrs['content']
    stamp = datetime.strptime(value, '%a %b %d %H:%M:%S UTC %Y').replace(tzinfo=timezone.utc)
    return req[1], stamp.timestamp()


def collect(*, since=None):
    """Join the filtered careers search to portal dates by requisition ID.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    jobs, pages = search_page(1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for batch, count in pool.map(search_page, range(2, pages + 1)):
            if count != pages:
                raise ValueError('KPMG search changed during collection. Please run the script again.')
            jobs.extend(batch)
    if len({job['id'] for job in jobs}) != len(jobs):
        raise ValueError('Duplicate KPMG vacancies across pages. Please run the script again.')
    if not jobs:
        return []
    titles = {job['title'] for job in jobs}
    rows, total = portal_page(0)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for batch, count in pool.map(portal_page, range(25, total, 25)):
            if count != total or rows.keys() & batch.keys():
                raise ValueError('KPMG portal changed during collection. Please run the script again.')
            rows.update(batch)
        if len(rows) != total:
            raise ValueError('Incomplete KPMG recruitment portal results. Please run the script again.')
        dates = dict(pool.map(posting_date, [url for url, title in rows.items()
                                            if title in titles]))
    for job in jobs:
        if job['id'] not in dates:
            raise ValueError('No KPMG portal posting date for requisition ' + job['id'] +
                             '. Please run the script again.')
        job['published'] = dates[job['id']]
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
        jobs: KPMG job records containing title, url, and published timestamp.
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
        styled('KPMG CAREERS', '1;31'),
        'London, UK',
        styled(prose("Full-time / Advisory / Experienced / All roles and levels"), '2'),
        styled(prose('Dates: posting date reported by KPMG recruitment portal'), '2'),
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
        print(f'{label}: Could not fetch KPMG Careers jobs.\n{error}', file=stderr)
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
