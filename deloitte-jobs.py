#!/usr/bin/env python3
"""Fetch Deloitte UK's full-time London roles across all business lines and grades.

Standalone: requires only Python 3.9+ and internet access.
No packages, browser, accounts, configuration, or companion files are needed.
WPFULL is Deloitte's full-time working-pattern marker in job descriptions.
Posting dates have day precision and are interpreted at midnight UK time.
"""

import concurrent.futures
import argparse
from datetime import datetime
from html.parser import HTMLParser
import os
import re
import shutil
import sys
import time
import textwrap
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


DAY_WINDOWS = (1, 3, 7, 14, 30)
SECONDS_PER_DAY = 86400


SEARCH_URL = 'https://apply.deloitte.co.uk/UKCareers/SearchJobs/?' + urllib.parse.urlencode({
    'search': 'WPFULL', '161': '228',  # London, including multi-office roles.
})


class Element:
    """HTML element with descendant text and attribute selectors."""

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
    """Read server-rendered HTML without executing JavaScript."""

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


def search_page(offset):
    """Read titles, locations, and posting dates from a public search page."""
    request = urllib.request.Request(SEARCH_URL + '&jobOffset=' + str(offset),
                                     headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        document = Page(response.read().decode('utf-8')).root
    try:
        results = next(document.find(class_name='section__content__results'))
        if ' '.join(results.text().split()) == 'No jobs found':
            return [], 0, 0
        rows = list(results.find('article', 'article--result'))
        summary = next(document.find(class_name='list-controls__text')).text()
        count = re.search(r'(?:of\s+)?(\d+)\s+results\b', summary)
        if count is None or not 1 <= int(count[1]) <= 10000 or not rows:
            raise ValueError('Unexpected Deloitte search pagination.')
        total = int(count[1])
        bounds = re.search(r'(\d+)-(\d+)\s+of\s+', summary)
        if bounds is not None:
            if int(bounds[1]) != offset + 1 or int(bounds[2]) != offset + len(rows):
                raise ValueError('Incomplete or incorrect Deloitte search page.')
        elif offset != 0 or len(rows) != total:
            raise ValueError('Incomplete Deloitte search page.')
        jobs = []
        for row in rows:
            link = next(next(row.find('h3')).find('a'))
            title = ' '.join(link.text().split())
            url = link.attrs['href']
            if (not title or not re.fullmatch(
                    r'https://apply\.deloitte\.co\.uk/UKCareers/JobDetail/[^/?#]+/\d+', url)):
                raise ValueError('Missing Deloitte job title or public detail URL.')
            subtitle = next(row.find(class_name='article__header__text__subtitle'))
            fields = [' '.join(node.text().split()) for node in subtitle.find('span')]
            if 'London' not in [city.strip() for city in fields[0].split(',')]:
                continue
            posted = next(field[8:] for field in fields if field.startswith('Posted: '))
            stamp = datetime.strptime(posted, '%d %b %Y').replace(tzinfo=ZoneInfo('Europe/London'))
            jobs.append({'title': title, 'url': url, 'published': stamp.timestamp()})
    except (KeyError, StopIteration, IndexError, TypeError) as error:
        raise ValueError('Deloitte vacancy search format could not be recognised.') from error
    return jobs, total, len(rows)


def collect(*, since=None):
    """Fetch full-time London roles from Deloitte's public UK careers search.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    jobs, total, size = search_page(0)
    if total == 0:
        return []
    collected = size
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for offset, (batch, count, row_count) in zip(
                range(size, total, size), pool.map(search_page, range(size, total, size))):
            if count != total or row_count != min(size, total - offset):
                raise ValueError('Deloitte results changed during collection. Please run the script again.')
            collected += row_count
            jobs.extend(batch)
    ids = [job['url'].rsplit('/', 1)[-1] for job in jobs]
    if collected != total or len(set(ids)) != len(ids):
        raise ValueError('Incomplete or duplicate Deloitte results. Please run the script again.')
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
        jobs: Deloitte job records containing title, url, and published timestamp.
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
        styled('DELOITTE CAREERS', '1;31'),
        'London, UK',
        styled(prose("Full-time / All roles and levels"), '2'),
        styled(prose('Dates: Deloitte posting date (day precision, UK time)'), '2'),
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
        print(f'{label}: Could not fetch Deloitte Careers jobs.\n{error}', file=stderr)
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
