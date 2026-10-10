import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


SCRIPT = Path(__file__).with_name('meta-jobs.py')


def load_script():
    spec = importlib.util.spec_from_file_location('meta_jobs', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BOOTSTRAP = '''<html><script type="application/json">{"define":[["LSD",[],{"token":"anonymous-token"},1]],"rsrcMap":{"jobs":{"type":"js","src":"https://static.xx.fbcdn.net/jobs.js"}},"bootloadable":{"CPJobSearch.react":{"r":["jobs"]}}}</script></html>'''
BUNDLE = '__d("CareersJobSearchResultsV2DataQuery_candidate_portalRelayOperation",[],(function(t,n,r,o,a,i){a.exports="27129360303422352"}),null);'


def posting(job_id, title='Business Engineer', locations=None, teams=None):
    return {'id': str(job_id), 'title': title,
            'locations': ['London, UK'] if locations is None else locations,
            'teams': ['Sales & Marketing'] if teams is None else teams, 'sub_teams': []}


def detail(title='Business Engineer', date='2026-09-05T12:00:00Z',
           employment='FULL_TIME', locations=None):
    data = {'@context': 'https://schema.org', '@type': 'JobPosting',
            'title': title, 'datePosted': date, 'employmentType': employment,
            'jobLocation': [{'@type': 'Place', 'name': location,
                             'address': {'addressLocality': location.split(',')[0],
                                         'addressCountry': 'GB'}}
                            for location in (['London, UK'] if locations is None else locations)]}
    return '<html><script type="application/ld+json">' + json.dumps(data) + '</script></html>'


class MetaHTTP:
    """Represent Meta's public HTTP responses at the network boundary."""

    def __init__(self, jobs, pages=None):
        self.jobs = jobs
        self.pages = pages or {}
        self.requests = []

    def __call__(self, request, timeout=30):
        self.requests.append(request)
        url = urlparse(request.full_url)
        if url.netloc == 'static.xx.fbcdn.net':
            body = BUNDLE
        elif url.path == '/jobsearch/':
            body = BOOTSTRAP
        elif url.path == '/api/graphql/':
            body = json.dumps({'data': {'job_search_with_featured_jobs_v2':
                                       {'all_jobs': self.jobs}}})
        elif url.path.startswith('/jobs/'):
            body = self.pages[url.path.strip('/').split('/')[1]]
        else:
            raise AssertionError('Unexpected external URL: ' + request.full_url)
        return io.BytesIO(body.encode())


class MetaJobsTests(unittest.TestCase):
    def test_command_prints_all_five_empty_groups_when_no_roles_match(self):
        result = subprocess.run([sys.executable, '-c', '''
import runpy, sys
from unittest.mock import patch
from test_meta_jobs import MetaHTTP
with patch('urllib.request.urlopen', side_effect=MetaHTTP([])):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], cwd=SCRIPT.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('0 roles in the last 30 days', result.stdout)
        self.assertEqual(result.stdout.count('No roles in this date range.'), 5)
        self.assertEqual(result.stderr, '')

    def test_command_reports_persistent_connection_failure_without_a_traceback(self):
        result = subprocess.run([sys.executable, '-c', '''
import runpy, sys
from unittest.mock import patch
with patch('urllib.request.urlopen', side_effect=OSError('Connection unavailable')), patch('time.sleep'):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], cwd=SCRIPT.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr,
                         'Error: Could not fetch Meta Careers jobs.\nConnection unavailable\n')

    def test_report_matches_the_existing_colours_wrapping_and_layout(self):
        module = load_script()
        spec = importlib.util.spec_from_file_location('openai_jobs', SCRIPT.with_name('openai-jobs.py'))
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        jobs = [{'title': 'A long title ' * 10, 'url': 'https://www.metacareers.com/jobs/101/',
                 'published': 1788609600}]
        for width in (24, 88, 140):
            for colour in (False, True):
                with self.subTest(width=width, colour=colour):
                    report = module.format_jobs(jobs, 1788696000, width=width, colour=colour)
                    original = reference.format_jobs(jobs, 1788696000, width=width, colour=colour)
                    self.assertEqual(report[report.index('1 role in the last 30 days'):],
                                     original[original.index('1 role in the last 30 days'):])
                    self.assertEqual('\033[' in report, colour)

    def test_command_prints_the_report_with_no_login_browser_or_installed_packages(self):
        result = subprocess.run([sys.executable, '-c', '''
import runpy, sys
from unittest.mock import patch
from test_meta_jobs import MetaHTTP, posting, detail
with patch('urllib.request.urlopen', side_effect=MetaHTTP([posting(101)], {'101': detail()})), patch('time.time', return_value=1788696000):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], cwd=SCRIPT.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('META CAREERS\nLondon, UK', result.stdout)
        self.assertIn('1 role in the last 30 days', result.stdout)
        self.assertIn('• Business Engineer  (1 Day Ago)', result.stdout)
        self.assertIn('  https://www.metacareers.com/jobs/101/', result.stdout)
        self.assertEqual(result.stdout.count('No roles in this date range.'), 4)
        self.assertNotIn('\033[', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_recovers_from_a_temporary_connection_failure_without_user_intervention(self):
        module = load_script()
        http = MetaHTTP([posting(101)], {'101': detail()})
        failures = {('www.metacareers.com', '/jobsearch/'),
                    ('static.xx.fbcdn.net', '/jobs.js'),
                    ('www.metacareers.com', '/api/graphql/'),
                    ('www.metacareers.com', '/jobs/101/')}

        def interrupted(request, timeout=30):
            url = urlparse(request.full_url)
            key = (url.netloc, url.path)
            if key in failures:
                failures.remove(key)
                raise OSError('Temporary connection interruption')
            return http(request, timeout)

        with patch('urllib.request.urlopen', side_effect=interrupted), patch('time.sleep'):
            jobs = module.collect()
        self.assertEqual(jobs, [{'title': 'Business Engineer',
                                'url': 'https://www.metacareers.com/jobs/101/',
                                'published': 1788609600}])

    def test_rejects_unrecognised_data_and_missing_dates_instead_of_partial_results(self):
        module = load_script()
        for feed, pages in [([{}], {}), ([posting('../bad')], {}),
                            ([posting(101, teams='Sales & Marketing')], {'101': detail()}),
                            (None, {}), ([posting(101)], {'101': detail(date=None)}),
                            ([posting(101)], {'101': detail(date='not a date')}),
                            ([posting(101)], {'101': detail(date='2026-09-05T12:00:00')}),
                            ([posting(101)], {'101': detail(title=None)}),
                            ([posting(101)], {'101': '<html>Job unavailable</html>'})]:
            with self.subTest(feed=feed, pages=pages):
                with patch('urllib.request.urlopen', side_effect=MetaHTTP(feed, pages)):
                    with self.assertRaises(ValueError):
                        module.collect()

    def test_rejects_duplicate_job_ids_instead_of_reporting_duplicate_results(self):
        module = load_script()
        http = MetaHTTP([posting(101), posting(101)], {'101': detail()})
        with patch('urllib.request.urlopen', side_effect=http):
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                module.collect()

    def test_collects_every_distinct_full_time_london_only_post_in_the_selected_teams(self):
        module = load_script()
        feed = [posting(101), posting(102),
                posting(103, locations=['London, UK', 'Dublin, Ireland']),
                posting(104, locations=['London, Ontario, Canada']),
                posting(105, teams=['Software Engineering']), posting(106),
                posting(107, teams=['Software Engineering', 'Infrastructure'])]
        http = MetaHTTP(feed, {'101': detail(date='2026-09-01T12:00:00Z'),
                               '102': detail(), '106': detail(employment='INTERN'),
                               '107': detail('Network Engineer')})
        with patch('urllib.request.urlopen', side_effect=http):
            jobs = module.collect()
        self.assertEqual([job['url'] for job in jobs],
                         ['https://www.metacareers.com/jobs/102/',
                          'https://www.metacareers.com/jobs/107/',
                          'https://www.metacareers.com/jobs/101/'])
        self.assertEqual([job['title'] for job in jobs],
                         ['Business Engineer', 'Network Engineer', 'Business Engineer'])
        self.assertEqual(jobs[0]['published'], 1788609600)
        api_requests = [r for r in http.requests if urlparse(r.full_url).path == '/api/graphql/']
        fields = parse_qs(api_requests[0].data.decode())
        filters = json.loads(fields['variables'][0])['search_input']
        self.assertEqual(filters['offices'], ['London, UK'])
        self.assertEqual(filters['roles'], ['Full time employment'])
        self.assertEqual(filters['teams'], ['Sales & Marketing', 'Product Management',
                         'Global Operations', 'Infrastructure', 'Communications & Public Policy',
                         'Business Development & Partnerships', 'Legal, Finance, Facilities & Admin'])
        self.assertIsNone(filters['results_per_page'])
        for request in http.requests:
            self.assertNotIn('Cookie', request.headers)
            self.assertNotIn('Authorization', request.headers)

    def test_report_uses_the_existing_five_groups_and_keeps_distinct_job_links(self):
        module = load_script()
        now = 1788696000
        ages = [0, 86400, 86401, 259200, 259201, 604800,
                604801, 1209600, 1209601, 2592000, 2592001, -1]
        jobs = [dict(title='Same title', url='https://www.metacareers.com/jobs/' + str(i),
                     published=now - age) for i, age in enumerate(ages)]
        report = module.format_jobs(list(reversed(jobs)), now)
        self.assertIn('META CAREERS\nLondon, UK', report)
        self.assertIn('10 roles in the last 30 days', report)
        headings = ['LAST 24 HOURS', '1-3 DAYS AGO', '3-7 DAYS AGO',
                    '7-14 DAYS AGO', '14-30 DAYS AGO']
        for i, heading in enumerate(headings):
            section = report.split(heading + '  2 roles')[1]
            if i < 4:
                section = section.split(headings[i + 1])[0]
            for number in (i * 2, i * 2 + 1):
                self.assertIn('  https://www.metacareers.com/jobs/' + str(number), section.splitlines())
        self.assertEqual(report.count('• Same title'), 10)
        self.assertNotIn('https://www.metacareers.com/jobs/10', report)
        self.assertNotIn('https://www.metacareers.com/jobs/11', report)
        self.assertNotIn('\033[', report)
        self.assertIn('Less than 1 Hour Ago', report)
        self.assertIn('30 Days Ago', report)


if __name__ == '__main__':
    unittest.main()
