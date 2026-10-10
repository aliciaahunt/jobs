import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


SCRIPT = Path(__file__).with_name('deloitte-jobs.py')


def load_script():
    spec = importlib.util.spec_from_file_location('deloitte_jobs', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def listing(rows, summary):
    articles = []
    for req, title, locations, date in rows:
        articles.append(f'''<article class="article article--result">
          <h3><a href="https://apply.deloitte.co.uk/UKCareers/JobDetail/Role/{req}">{title}</a></h3>
          <div class="article__header__text__subtitle"><span>{locations}</span>
          <span>Technology &amp; Transformation</span><span>Posted: {date}</span></div>
          <a href="https://apply.deloitte.co.uk/UKCareers/Login?jobId={req}">Apply now</a>
        </article>''')
    content = ''.join(articles) or '<p>No jobs found</p>'
    return (f'<div class="list-controls__text">{summary}</div>'
            f'<div class="section__content__results">{content}</div>').encode()


class DeloitteJobsTests(unittest.TestCase):
    def test_collects_all_full_time_london_roles_newest_first_without_login(self):
        module = load_script()
        source = listing([
            ('25121', 'Agile Delivery Lead', 'Bristol, London, Manchester', '28 Sep 2026'),
            ('25331', 'AI &amp; Cloud Architect', 'London', '30 Sep 2026'),
            ('88888', 'Londonderry role', 'Londonderry', '30 Sep 2026'),
            ('99999', 'Bristol role', 'Bristol', '30 Sep 2026'),
        ], '1-4 of 4 results')

        def respond(request, timeout):
            url = urlsplit(request.full_url)
            self.assertEqual(url.scheme, 'https')
            self.assertEqual(url.netloc, 'apply.deloitte.co.uk')
            self.assertEqual(url.path.rstrip('/'), '/UKCareers/SearchJobs')
            query = parse_qs(url.query)
            self.assertEqual(query['search'], ['WPFULL'])
            self.assertEqual(query['161'], ['228'])
            self.assertEqual(query['jobOffset'], ['0'])
            self.assertNotIn('162', query)  # Every business line.
            self.assertNotIn('3884[]', query)  # Every grade.
            self.assertIsNone(request.data)
            return io.BytesIO(source)

        with patch('urllib.request.urlopen', side_effect=respond):
            jobs = module.collect()
        self.assertEqual([job['title'] for job in jobs],
                         ['AI & Cloud Architect', 'Agile Delivery Lead'])
        self.assertEqual(jobs[0]['url'],
                         'https://apply.deloitte.co.uk/UKCareers/JobDetail/Role/25331')
        # 30 September 2026 at midnight BST = 29 September at 23:00 UTC.
        self.assertEqual(jobs[0]['published'], 1790722800)

    def test_collects_every_page_and_keeps_distinct_roles_with_the_same_title(self):
        module = load_script()
        first = listing([(str(10000 + i), 'Consultant', 'London', '28 Sep 2026')
                         for i in range(20)], '1-20 of 21 results')
        last = listing([('20000', 'Consultant', 'Manchester, London', '09 Oct 2026')],
                       '21-21 of 21 results')

        def respond(request, timeout):
            query = parse_qs(urlsplit(request.full_url).query)
            self.assertEqual(query['search'], ['WPFULL'])
            self.assertEqual(query['161'], ['228'])
            offset = int(query['jobOffset'][0])
            self.assertIn(offset, (0, 20))
            return io.BytesIO(first if offset == 0 else last)

        with patch('urllib.request.urlopen', side_effect=respond):
            jobs = module.collect()
        self.assertEqual(len(jobs), 21)
        self.assertEqual(jobs[0]['url'],
                         'https://apply.deloitte.co.uk/UKCareers/JobDetail/Role/20000')
        self.assertEqual(len({job['url'] for job in jobs}), 21)

    def test_recognises_deloittes_empty_search(self):
        module = load_script()
        with patch('urllib.request.urlopen',
                   return_value=io.BytesIO(listing([], 'Filters applied (Reset)'))):
            self.assertEqual(module.collect(), [])

    def test_rejects_unrecognised_or_malformed_search_instead_of_partial_results(self):
        module = load_script()
        good = listing([('25121', 'Consultant', 'London', '28 Sep 2026')], '1 results')
        invalid = [
            b'<html>Login required</html>',
            good.replace(b'1 results', b'Unknown results'),
            good.replace(b'28 Sep 2026', b'not a date'),
            good.replace(b'Consultant', b' '),
            good.replace(b'/JobDetail/Role/25121', b'/Login?jobId=25121'),
            good.replace(b'<span>Posted: 28 Sep 2026</span>', b''),
            good.replace(b'1 results', b'1-1 of 10001 results'),
        ]
        for source in invalid:
            with self.subTest(source=source[:100]):
                with patch('urllib.request.urlopen', return_value=io.BytesIO(source)):
                    with self.assertRaises(ValueError):
                        module.collect()

    def test_rejects_missing_duplicate_or_changing_pages(self):
        module = load_script()
        first_rows = [(str(10000 + i), 'Consultant', 'London', '28 Sep 2026')
                      for i in range(20)]
        first = listing(first_rows, '1-20 of 21 results')
        scenarios = [
            (first, listing([], 'Filters applied (Reset)')),
            (listing(first_rows, '1-20 of 22 results'),
             listing([('20000', 'Consultant', 'London', '28 Sep 2026')], '21-21 of 22 results')),
            (first, listing([first_rows[0]], '21-21 of 21 results')),
            (first, listing([('20000', 'Consultant', 'London', '28 Sep 2026')],
                            '21-21 of 22 results')),
            (listing([first_rows[0], first_rows[0]], '1-2 of 2 results'), None),
            (first, listing([('20000', 'Consultant', 'London', '28 Sep 2026')],
                            '1-1 of 21 results')),
        ]
        for initial, later in scenarios:
            with self.subTest(later=later):
                def respond(request, timeout):
                    offset = parse_qs(urlsplit(request.full_url).query)['jobOffset'][0]
                    return io.BytesIO(initial if offset == '0' else later)
                with patch('urllib.request.urlopen', side_effect=respond):
                    with self.assertRaises(ValueError):
                        module.collect()

    def test_report_matches_existing_ui_and_non_overlapping_date_groups(self):
        module = load_script()
        spec = importlib.util.spec_from_file_location('openai_reference',
                                                     SCRIPT.with_name('openai-jobs.py'))
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        now = 1791655200
        ages = [0, 86400, 86401, 259200, 259201, 604800,
                604801, 1209600, 1209601, 2592000, 2592001, -1]
        jobs = [dict(title='Role ' + str(i) + ' with a title that wraps in narrow terminals',
                     url='https://apply.deloitte.co.uk/UKCareers/JobDetail/Role/' + str(i),
                     published=now - age) for i, age in enumerate(ages)]
        for width, colour in [(88, False), (24, False), (150, True)]:
            with self.subTest(width=width, colour=colour):
                expected = reference.format_jobs(list(reversed(jobs)), now,
                                                 colour=colour, width=width)
                # Only the company, filter caption, and date provenance differ.
                import textwrap
                report_width = max(24, min(width, 96))
                expected = expected.replace('OPENAI CAREERS', 'DELOITTE CAREERS')
                for old, new in [
                    ('Full-time / All levels and degrees (feed has no filters)',
                     'Full-time / All roles and levels'),
                    ('Dates: last published, which may include republication',
                     'Dates: Deloitte posting date (day precision, UK time)'),
                ]:
                    expected = expected.replace(textwrap.fill(old, width=report_width),
                                                textwrap.fill(new, width=report_width))
                actual = module.format_jobs(list(reversed(jobs)), now,
                                             colour=colour, width=width)
                self.assertEqual(actual, expected)
                self.assertIn('10 roles in the last 30 days', actual)

    def test_command_prints_report_and_exits_successfully_without_prompts(self):
        result = subprocess.run([sys.executable, '-c', '''
import io, runpy, sys
from unittest.mock import patch
source = b'<div class="list-controls__text">Filters applied (Reset)</div><div class="section__content__results">No jobs found</div>'
with patch('urllib.request.urlopen', return_value=io.BytesIO(source)):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DELOITTE CAREERS\nLondon, UK', result.stdout)
        self.assertIn('Full-time / All roles and levels', result.stdout)
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
''', str(SCRIPT)], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr,
                         'Error: Could not fetch Deloitte Careers jobs.\nConnection unavailable\n')


if __name__ == '__main__':
    unittest.main()
