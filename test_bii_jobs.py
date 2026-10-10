import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name('bii-jobs.py')
BASE = 'https://isw.changeworknow.co.uk/bii/vms/e/careers/'


def load_script():
    spec = importlib.util.spec_from_file_location('bii_jobs', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def listing(key, title, location='United Kingdom, London', contract='Permanent'):
    return f'''<div class="position_opening"><div class="panel-heading">
    <h1 class="panel-title"><a href="/bii/vms/e/careers/positions/{key}">{title}</a></h1>
    </div><div class="panel-body"><p><strong>Location</strong>: {location}</p>
    <p><strong>Employment Type</strong>: {contract}</p></div></div>'''


def search(*rows, pagination=''):
    return ('<div class="position-search-results">All our vacancies are displayed below.'
            + ''.join(rows) + '<div class="pagination_container">' + pagination
            + '</div></div>')


def detail(title, location='United Kingdom, London', contract='Permanent', prose=''):
    return f'''<div id="position-info-box"><p><strong>Location</strong>: {location}</p>
    <p><strong>Employment Type</strong>: {contract}</p></div>
    <div id="position_display"><main id="main-content"><h1 class="job-title">{title}</h1>
    <p>{prose}</p></main></div>'''


def feed(*items):
    return ('<rss version="2.0"><channel><title>British International Investment Careers</title>'
            + ''.join(f'<item><title>{title}</title><link>{BASE}positions/{key}</link>'
                      f'<pubDate>{date}</pubDate></item>' for key, title, date in items)
            + '</channel></rss>')


def http(pages):
    def open_url(request, timeout=30):
        url = request.full_url if hasattr(request, 'full_url') else request
        if url not in pages:
            raise AssertionError(f'Unexpected HTTP request: {url}')
        return io.BytesIO(pages[url].encode())
    return patch('urllib.request.urlopen', side_effect=open_url)


class BiiJobsTests(unittest.TestCase):
    def test_collects_all_london_employee_roles_including_fixed_term_and_secondary_offices(self):
        module = load_script()
        pages = {
            BASE + 'search/new': search(
                listing('data', 'Azure Data Engineer'),
                listing('fund', 'Fund Operations Associate, 12-month FTC', contract='Fixed Term'),
                listing('director', 'Investment Director', 'London, Nairobi, Johannesburg'),
                listing('africa', 'Investment Associate', 'Lagos &amp; Nairobi'),
                listing('canada', 'Analyst', 'London, Ontario, Canada')),
            BASE + 'search.rss': feed(
                ('data', 'Azure Data Engineer', 'Sun, 27 Sep 2026 00:00:00 +0100'),
                ('fund', 'Fund Operations Associate, 12-month FTC', 'Sun, 27 Sep 2026 00:00:00 +0100'),
                ('director', 'Investment Director', 'Thu, 01 Oct 2026 00:00:00 +0100')),
            BASE + 'positions/data': detail('Azure Data Engineer'),
            BASE + 'positions/fund': detail('Fund Operations Associate, 12-month FTC', contract='Fixed Term'),
            BASE + 'positions/director': detail('Investment Director', 'London, Nairobi, Johannesburg'),
        }
        with http(pages):
            jobs = module.collect()
        self.assertEqual(jobs, [
            {'title': 'Investment Director', 'url': BASE + 'positions/director', 'published': 1790809200.0},
            {'title': 'Azure Data Engineer', 'url': BASE + 'positions/data', 'published': 1790463600.0},
            {'title': 'Fund Operations Associate, 12-month FTC', 'url': BASE + 'positions/fund',
             'published': 1790463600.0},
        ])

    def test_excludes_part_time_and_unconfirmed_non_employee_roles(self):
        module = load_script()
        cases = [
            ('part', 'Part-time Analyst', 'Permanent', ''),
            ('hours', 'Finance Manager', 'Fixed Term', 'Working pattern: part time, 3 days per week.'),
            ('consultant', 'Consultant', 'Consultant', ''),
            ('intern', 'Summer Intern', 'Intern', ''),
            ('full', 'Full-time Intern', 'Intern', 'Full-time internship.'),
            ('director', 'Director', 'Permanent', 'All levels and backgrounds welcome.'),
        ]
        pages = {
            BASE + 'search/new': search(*(listing(key, title, contract=contract)
                                         for key, title, contract, _ in cases)),
            BASE + 'search.rss': feed(*((key, title, 'Thu, 01 Oct 2026 00:00:00 +0100')
                                       for key, title, _, _ in cases)),
        }
        pages.update({BASE + 'positions/' + key: detail(title, contract=contract, prose=prose)
                      for key, title, contract, prose in cases})
        with http(pages):
            jobs = module.collect()
        self.assertEqual([job['title'] for job in jobs], ['Full-time Intern', 'Director'])

    def test_collects_london_roles_on_later_search_pages(self):
        module = load_script()
        pages = {
            BASE + 'search/new': search(listing('first', 'Analyst'), pagination=
                '<a href="/bii/vms/e/careers/search/new?page=2">2</a>'),
            BASE + 'search/new?page=2': search(listing('second', 'Managing Director'), pagination=
                '<a href="/bii/vms/e/careers/search/new?page=1">1</a>'
                '<a href="/bii/vms/e/careers/search/new?page=2">2</a>'),
            BASE + 'search.rss': feed(
                ('first', 'Analyst', 'Thu, 01 Oct 2026 00:00:00 +0100'),
                ('second', 'Managing Director', 'Fri, 02 Oct 2026 00:00:00 +0100')),
            BASE + 'positions/first': detail('Analyst'),
            BASE + 'positions/second': detail('Managing Director'),
        }
        with http(pages):
            self.assertEqual([job['title'] for job in module.collect()], ['Managing Director', 'Analyst'])

    def test_rejects_incomplete_changed_or_unrecognised_sources(self):
        module = load_script()
        valid = {
            BASE + 'search/new': search(listing('data', 'Engineer')),
            BASE + 'search.rss': feed(('data', 'Engineer', 'Thu, 01 Oct 2026 00:00:00 +0100')),
            BASE + 'positions/data': detail('Engineer'),
        }
        cases = [
            {BASE + 'search.rss': '<html>Temporarily unavailable</html>'},
            {BASE + 'search.rss': feed(('data', 'Engineer', 'not a date'))},
            {BASE + 'search.rss': feed(('data', 'Engineer', 'Thu, 01 Oct 2026 00:00:00'))},
            {BASE + 'search.rss': feed()},
            {BASE + 'search.rss': feed(
                ('data', 'Engineer', 'Thu, 01 Oct 2026 00:00:00 +0100'),
                ('data', 'Engineer', 'Thu, 01 Oct 2026 00:00:00 +0100'))},
            {BASE + 'search/new': '<html>Login required</html>'},
            {BASE + 'search/new': search(listing('data', 'Engineer'), listing('data', 'Engineer'))},
            {BASE + 'search/new': search(listing('data', ''))},
            {BASE + 'search/new': search(listing('data', 'Engineer', location=''))},
            {BASE + 'search/new': search(listing('data', 'Engineer').replace(
                '/bii/vms/e/careers/positions/data', 'https://example.com/login'))},
            {BASE + 'positions/data': '<html>That vacancy is no longer available</html>'},
            {BASE + 'positions/data': detail('Different vacancy')},
            {BASE + 'positions/data': detail('Engineer', 'Mumbai Office')},
        ]
        for changes in cases:
            with self.subTest(changes=changes), http(dict(valid, **changes)):
                with self.assertRaises(ValueError):
                    module.collect()

    def test_report_matches_existing_ui_and_places_roles_in_non_overlapping_date_groups(self):
        module = load_script()
        now = 1791648000
        ages = [0, 86400, 86401, 259200, 259201, 604800,
                604801, 1209600, 1209601, 2592000, 2592001, -1]
        jobs = [dict(title='Role ' + str(i), url='https://example.com/' + str(i),
                     published=now - age) for i, age in enumerate(ages)]
        report = module.format_jobs(list(reversed(jobs)), now)
        self.assertIn('BRITISH INTERNATIONAL INVESTMENT CAREERS\nLondon, UK', report)
        self.assertIn('Full-time / All roles and levels', report)
        self.assertIn('Permanent + Fixed Term assumed full-time unless marked part-time', report)
        self.assertIn('Dates: RSS publication date (day precision)', report)
        self.assertIn('10 roles in the last 30 days', report)
        self.assertIn('12 matched / Updated ', report)
        headings = ['LAST 24 HOURS', '1-3 DAYS AGO', '3-7 DAYS AGO',
                    '7-14 DAYS AGO', '14-30 DAYS AGO']
        for i, heading in enumerate(headings):
            section = report.split(heading + '  2 roles')[1]
            if i < 4:
                section = section.split(headings[i + 1])[0]
            for number in (i * 2, i * 2 + 1):
                self.assertIn('• Role ' + str(number) + '  (', section)
                self.assertEqual(report.splitlines().count('  https://example.com/' + str(number)), 1)
        self.assertNotIn('• Role 10', report)
        self.assertNotIn('• Role 11', report)
        self.assertNotIn('\033[', report)
        self.assertIn('Less than 1 Hour Ago', report)
        self.assertIn('30 Days Ago', report)

    def test_command_prints_empty_report_and_exits_successfully_without_colour(self):
        pages = {BASE + 'search/new': search('No vacancies found.'),
                 BASE + 'search.rss': feed()}
        result = subprocess.run([sys.executable, '-c', f'''
import io, runpy, sys
from unittest.mock import patch
pages = {pages!r}
with patch('urllib.request.urlopen', side_effect=lambda request, timeout: io.BytesIO(pages[request.full_url].encode())):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('BRITISH INTERNATIONAL INVESTMENT CAREERS', result.stdout)
        self.assertIn('0 roles in the last 30 days', result.stdout)
        self.assertEqual(result.stdout.count('No roles in this date range.'), 5)
        self.assertNotIn('\033[', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_command_reports_fetch_failure_without_a_traceback(self):
        result = subprocess.run([sys.executable, '-c', '''
import runpy, sys
from unittest.mock import patch
with patch('urllib.request.urlopen', side_effect=OSError('Connection unavailable')):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr,
                         'Error: Could not fetch British International Investment Careers jobs.\n'
                         'Connection unavailable\n')


if __name__ == '__main__':
    unittest.main()
