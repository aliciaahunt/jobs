#!/usr/bin/env python3
"""Fetch British International Investment's London vacancies.

Standalone: requires only Python 3.8+ and internet access.
No packages, browser, login, configuration, or companion files are needed.
BII does not publish working hours: Permanent and Fixed Term are assumed
full-time unless explicitly marked part-time. Dates come from its public RSS.
"""

import argparse
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import os
import re
import shutil
import sys
import time
import textwrap
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


BASE_URL = 'https://isw.changeworknow.co.uk/bii/vms/e/careers/'
SEARCH_URL = BASE_URL + 'search/new'
FEED_URL = BASE_URL + 'search.rss'
DAY_WINDOWS = (1, 3, 7, 14, 30)
SECONDS_PER_DAY = 86400


class Element:
    """A server-rendered HTML element with text and descendant selectors."""

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
    """Read HTML without a browser or executing JavaScript."""

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
    """Read a public recruitment-board response using ordinary HTTPS."""
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode('utf-8')


def fields(element):
    """Read labelled vacancy metadata, separate from the job description."""
    result = {}
    for paragraph in element.find('p'):
        labels = list(paragraph.find('strong'))
        if labels:
            label = ' '.join(labels[0].text().split())
            value = ' '.join(paragraph.text().split())[len(label):].lstrip(': ')
            result[label] = value
    return result


def london(location):
    """Include London among multiple offices, excluding London in Canada."""
    value = location.casefold()
    return (not re.search(r'\b(?:canada|ontario)\b', value)
            and any(re.fullmatch(r'london(?:\s+(?:office|uk|united kingdom))?', city.strip())
                    for city in re.split(r'[,;/|&]|\band\b', value)))


def collect(*, since=None):
    """Fetch all London employee roles, newest publication first.

    since: When set, return only the inclusive 24-hour window starting here.
    """
    try:
        feed = ET.fromstring(fetch(FEED_URL))
        if feed.tag != 'rss' or feed.findtext('./channel/title') != 'British International Investment Careers':
            raise ValueError('BII publication feed could not be recognised.')
        dates = {}
        for item in feed.findall('./channel/item'):
            url = item.findtext('link')
            stamp = parsedate_to_datetime(item.findtext('pubDate'))
            if stamp.tzinfo is None:
                raise ValueError('BII publication date has no timezone.')
            if url in dates:
                raise ValueError('Duplicate BII publication dates. Please run the script again.')
            dates[url] = stamp.timestamp()
        rows = []
        pending = [SEARCH_URL]
        seen_pages = {1}
        for page_url in pending:
            document = Page(fetch(page_url)).root
            results = next(document.find(class_name='position-search-results'))
            rows.extend(results.find(class_name='position_opening'))
            for pagination in results.find(class_name='pagination_container'):
                for link in pagination.find('a'):
                    url = urllib.parse.urljoin(page_url, link.attrs['href'])
                    parsed = urllib.parse.urlsplit(url)
                    page = urllib.parse.parse_qs(parsed.query).get('page', ['1'])[0]
                    if (parsed.scheme != 'https' or parsed.netloc != 'isw.changeworknow.co.uk'
                            or not re.fullmatch(r'/bii/vms/e/careers/search(?:/new|\.html)?', parsed.path)
                            or not page.isdigit() or not 1 <= int(page) <= 1000):
                        raise ValueError('Unrecognised BII search pagination.')
                    if int(page) not in seen_pages:
                        pending.append(url)
                        seen_pages.add(int(page))
        jobs = []
        seen = set()
        for row in rows:
            metadata = fields(row)
            if not metadata['Location'] or not metadata['Employment Type']:
                raise ValueError('Missing BII location or employment type.')
            link = next(next(row.find(class_name='panel-title')).find('a'))
            title = ' '.join(link.text().split())
            url = urllib.parse.urljoin(BASE_URL, link.attrs['href'])
            if (not title or not re.fullmatch(re.escape(BASE_URL) + r'positions/[A-Za-z0-9_-]+', url)):
                raise ValueError('Missing BII title or unrecognised public vacancy URL.')
            if url in seen:
                raise ValueError('Duplicate BII vacancies. Please run the script again.')
            seen.add(url)
            if not london(metadata['Location']):
                continue
            # RSS dates are available before the expensive vacancy detail request.
            if (since is not None and url in dates
                    and not since <= dates[url] <= since + SECONDS_PER_DAY):
                continue
            details = Page(fetch(url)).root
            info = fields(next(details.find(id='position-info-box')))
            main = next(details.find(id='main-content'))
            detail_title = ' '.join(next(main.find(class_name='job-title')).text().split())
            if (detail_title != title or info['Location'] != metadata['Location']
                    or info['Employment Type'] != metadata['Employment Type']):
                raise ValueError('BII results changed during collection. Please run the script again.')
            working_text = title + ' ' + ' '.join(info.values()) + ' ' + main.text()
            if re.search(r'\b(?:part[\s\-\u2010-\u2015]*time|job[\s\-]*shar(?:e|ing))\b',
                         working_text, re.IGNORECASE):
                continue
            explicit_full_time = re.search(r'\bfull[\s\-\u2010-\u2015]*time\b', working_text, re.IGNORECASE)
            if info['Employment Type'] not in ('Permanent', 'Fixed Term') and not explicit_full_time:
                continue
            if url not in dates:
                raise ValueError('BII RSS is missing a matching vacancy date. Please run the script again.')
            jobs.append({'title': title, 'url': url, 'published': dates[url]})
    except (KeyError, StopIteration, TypeError, AttributeError, ET.ParseError) as error:
        raise ValueError('BII vacancy data could not be recognised. Please run the script again.') from error
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
        jobs: BII job records containing title, url, and published timestamp.
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
        styled('BRITISH INTERNATIONAL INVESTMENT CAREERS', '1;31'),
        'London, UK',
        styled(prose('Full-time / All roles and levels'), '2'),
        styled(prose('Permanent + Fixed Term assumed full-time unless marked part-time'), '2'),
        styled(prose('Dates: RSS publication date (day precision)'), '2'),
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
        print(f'{label}: Could not fetch British International Investment Careers jobs.\n{error}',
              file=stderr)
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
