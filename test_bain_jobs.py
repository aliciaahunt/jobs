import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


SCRIPT = Path(__file__).with_name('bain-jobs.py')


def load_script():
    spec = importlib.util.spec_from_file_location('bain_jobs', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def posting(job_id='110034', title='Associate Director, Bain Academy', **changes):
    row = {'JobId': job_id, 'JobTitle': title,
           'Link': '/careers/find-a-role/position/?jobid=' + job_id,
           'Location': [' Boston ', ' London ', ' Zurich'],
           'EmployeeType': 'Permanent Full-Time'}
    row.update(changes)
    return row


def search_response(rows, total=None):
    return io.BytesIO(json.dumps({'results': rows, 'totalResults':
                                 len(rows) if total is None else total}).encode())


def detail_response(row, date='2026-10-09'):
    data = {'@context': 'https://schema.org/', '@type': 'JobPosting',
            'title': row['JobTitle'], 'datePosted': date,
            'employmentType': row['EmployeeType']}
    return io.BytesIO(('<html><script type="application/ld+json">' +
                      json.dumps(data) + '</script></html>').encode())


class BainJobsTests(unittest.TestCase):
    def test_collects_all_full_time_london_categories_including_secondary_offices(self):
        module = load_script()
        rows = [posting(), posting('110043', 'Recruiter',
                                   EmployeeType='Temporary Full-Time'),
                posting('110044', 'Contract conversion',
                        EmployeeType='Contract to Permanent (Full-Time)'),
                posting('10463', 'Summer Associate', EmployeeType='Intern (Full-Time)',
                        Link='/careers/work-with-us/internships-programs/summer-associate/'),
                posting('110046', 'Part time', EmployeeType='Permanent Part-Time'),
                posting('110047', 'Canada', Location=['London, Ontario']),
                posting('110048', 'Paris', Location=['Paris'])]
        with patch('urllib.request.urlopen', side_effect=[search_response(rows),
                detail_response(rows[0]), detail_response(rows[1], '2026-09-25'),
                detail_response(rows[2], '2026-10-05'), detail_response(rows[3], '')]):
            jobs = module.collect()
        self.assertEqual([job['title'] for job in jobs],
                         ['Associate Director, Bain Academy', 'Contract conversion',
                          'Recruiter', 'Summer Associate'])
        self.assertEqual(jobs[0]['published'], 1791504000)
        self.assertEqual(jobs[0]['url'],
                         'https://www.bain.com/careers/find-a-role/position/?jobid=110034')
        self.assertIsNone(jobs[-1]['published'])
        self.assertEqual(jobs[-1]['url'], 'https://www.bain.com/careers/work-with-us/'
                         'internships-programs/summer-associate/')

    def test_collects_every_search_page_with_the_requested_filters(self):
        module = load_script()
        first, second = posting(), posting('110043', 'Recruiter')

        def public_site(request, timeout):
            url = urlparse(request.full_url)
            params = parse_qs(url.query, keep_blank_values=True)
            if url.path == '/en/api/jobsearch/keyword/get':
                self.assertEqual(params['filters'],
                                 ['offices(231)|employmenttype(E00,E10,E30,E60)'])
                self.assertEqual(params['searchValue'], [''])
                self.assertEqual(params['results'], ['100'])
                if params['start'] == ['0']:
                    return search_response([first], total=2)
                if params['start'] == ['1']:
                    return search_response([second], total=2)
                self.fail('Unexpected search page')
            return detail_response(first if params['jobid'] == ['110034'] else second)

        with patch('urllib.request.urlopen', side_effect=public_site):
            jobs = module.collect()
        self.assertEqual({job['title'] for job in jobs},
                         {'Associate Director, Bain Academy', 'Recruiter'})

    def test_rejects_incomplete_changing_duplicate_or_malformed_search_results(self):
        module = load_script()
        cases = [[io.BytesIO(b'{}')],
                 [search_response([posting()], total=2), search_response([], total=2)],
                 [search_response([posting()], total=2), search_response([], total=3)],
                 [search_response([posting(), posting()])],
                 [search_response([posting(JobId='../login')])],
                 [search_response([posting(Location='London')])],
                 [search_response([posting(EmployeeType=None)])],
                 [search_response([posting(JobTitle='')])],
                 [search_response([posting(Link='https://example.com/login')])],
                 [search_response([posting()], total=0)],
                 [search_response([], total=True)]]
        for responses in cases:
            with self.subTest(responses=responses), \
                    patch('urllib.request.urlopen', side_effect=responses):
                with self.assertRaises(ValueError):
                    module.collect()

    def test_report_uses_the_existing_non_overlapping_date_groups_and_layout(self):
        module = load_script()
        now = 1791648000
        ages = [0, 86400, 86401, 259200, 259201, 604800,
                604801, 1209600, 1209601, 2592000, 2592001, -1]
        jobs = [dict(title='Role ' + str(i), url='https://example.com/' + str(i),
                     published=now - age) for i, age in enumerate(ages)]
        jobs.append(dict(title='Evergreen', url='https://example.com/evergreen',
                         published=None))
        report = module.format_jobs(list(reversed(jobs)), now)
        self.assertIn('BAIN CAREERS\nLondon, UK', report)
        self.assertIn('Full-time / All roles, levels and degrees', report)
        self.assertIn('10 roles in the last 30 days', report)
        self.assertIn('13 matched / Updated', report)
        headings = ['LAST 24 HOURS', '1-3 DAYS AGO', '3-7 DAYS AGO',
                    '7-14 DAYS AGO', '14-30 DAYS AGO', 'DATE UNAVAILABLE']
        for i, heading in enumerate(headings[:5]):
            section = report.split(heading + '  2 roles')[1].split(headings[i + 1])[0]
            for number in (i * 2, i * 2 + 1):
                self.assertIn('• Role ' + str(number) + '  (', section)
                self.assertEqual(report.splitlines().count('  https://example.com/' + str(number)), 1)
        self.assertNotIn('• Role 10', report)
        self.assertNotIn('• Role 11', report)
        self.assertNotIn('\033[', report)
        self.assertIn('Less than 1 Hour Ago', report)
        self.assertIn('30 Days Ago', report)
        self.assertIn('DATE UNAVAILABLE  1 role\n\n• Evergreen  (Date unavailable)', report)
        self.assertEqual(report.count('  https://example.com/evergreen'), 1)

    def test_python_command_prints_the_report_without_login_or_browser(self):
        result = subprocess.run([sys.executable, '-c', '''
import io, runpy, sys
from unittest.mock import patch
with patch('urllib.request.urlopen', return_value=io.BytesIO(
        b'{"results": [], "totalResults": 0}')):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('BAIN CAREERS\nLondon, UK', result.stdout)
        self.assertIn('0 roles in the last 30 days', result.stdout)
        self.assertEqual(result.stdout.count('No roles in this date range.'), 5)
        self.assertNotIn('DATE UNAVAILABLE', result.stdout)
        self.assertNotIn('\033[', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_command_reports_fetch_failures_without_a_traceback(self):
        module = load_script()
        failures = [OSError('Connection unavailable'),
                    io.BytesIO(b'\xff'), io.BytesIO(b'{"results": []}')]
        for failure in failures:
            stdout, stderr = io.StringIO(), io.StringIO()
            options = ({'side_effect': failure} if isinstance(failure, Exception)
                       else {'return_value': failure})
            with self.subTest(failure=failure), patch('urllib.request.urlopen', **options), \
                    patch('sys.stdout', stdout), patch('sys.stderr', stderr):
                code = module.main([])
            self.assertEqual(code, 1)
            self.assertEqual(stdout.getvalue(), '')
            self.assertTrue(stderr.getvalue().startswith(
                'Error: Could not fetch Bain Careers jobs.\n'))
            self.assertNotIn('Traceback', stderr.getvalue())

    def test_decodes_html_entities_in_job_detail_titles(self):
        module = load_script()
        row = posting(title='Specialist, Vendor Operations & Innovation – ETF')
        details = detail_response(posting(
            title='Specialist, Vendor Operations &amp; Innovation &#x2013; ETF'))
        with patch('urllib.request.urlopen', side_effect=[search_response([row]), details]):
            jobs = module.collect()
        self.assertEqual(jobs[0]['title'], 'Specialist, Vendor Operations & Innovation – ETF')

    def test_keeps_undated_internships_when_bain_embeds_literal_newlines_in_json(self):
        module = load_script()
        row = posting('10463', 'Summer Associate', EmployeeType='Intern (Full-Time)')
        source = '''<script type="application/ld+json">{
            "@type": "JobPosting", "title": "Summer Associate",
            "employmentType": "Intern (Full-Time)", "datePosted": "",
            "description": "Summer training.
            Work on a case team."}</script>'''
        with patch('urllib.request.urlopen', side_effect=[search_response([row]),
                                                         io.BytesIO(source.encode())]):
            jobs = module.collect()
        self.assertEqual(jobs, [{'title': 'Summer Associate',
                                'url': 'https://www.bain.com/careers/find-a-role/'
                                       'position/?jobid=10463', 'published': None}])

    def test_rejects_invalid_dates_and_missing_or_changed_job_details(self):
        module = load_script()
        row = posting()
        invalid = [detail_response(row, 'not a date'),
                   detail_response(row, '2026-2-3'),
                   detail_response(row, None),
                   detail_response(posting(title='Changed title')),
                   detail_response(posting(EmployeeType='Permanent Part-Time')),
                   io.BytesIO(b'<html>Login required</html>'),
                   io.BytesIO(b'<script type="application/ld+json">{}</script>'),
                   io.BytesIO(b'<script type="application/ld+json">null</script>')]
        for detail in invalid:
            with self.subTest(detail=detail), patch('urllib.request.urlopen',
                    side_effect=[search_response([row]), detail]):
                with self.assertRaises(ValueError):
                    module.collect()


if __name__ == '__main__':
    unittest.main()
