import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


SCRIPT = Path(__file__).with_name('apple-jobs.py')


def load_script():
    spec = importlib.util.spec_from_file_location('apple_jobs', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def posting(number='200688187-2114', title='Engineer', **changes):
    result = dict(reqId=number, postingTitle=title,
                  transformedPostingTitle='engineer', standardWeeklyHours=35,
                  postDateInGMT='2026-10-09T17:42:37.343Z',
                  team={'teamCode': 'HRDWR'})
    result.update(changes)
    return result


def page_response(jobs, total=None, page=1, location='postLocation-LND'):
    data = {'loaderData': {'search': {
        'searchResults': jobs, 'totalRecords': len(jobs) if total is None else total,
        'page': page, 'filters': {'locations': [{'id': location}]}}}}
    script = 'window.__staticRouterHydrationData = JSON.parse(' + json.dumps(json.dumps(data)) + ');'
    return io.BytesIO(('<html><script>' + script + '</script></html>').encode())


class AppleJobsTests(unittest.TestCase):
    def test_recovers_from_changed_results_and_returns_a_complete_fresh_search(self):
        module = load_script()
        with patch('urllib.request.urlopen', side_effect=[
                page_response([posting('200000001-2114', 'Old snapshot')], total=2),
                page_response([posting('200000002-2114', 'New second page')], total=3, page=2),
                page_response([posting('200000003-2114', 'Current first'),
                               posting('200000004-2114', 'Current second')], total=3),
                page_response([posting('200000005-2114', 'Current last')], total=3, page=2)]):
            jobs = module.collect()
        self.assertEqual([job['title'] for job in jobs],
                         ['Current first', 'Current second', 'Current last'])

    def test_collects_all_full_time_hours_london_results_across_pages_newest_first(self):
        module = load_script()
        requests = []

        def apple_http(request, timeout):
            requests.append(request)
            query = parse_qs(urlparse(request.full_url).query)
            self.assertEqual(query['location'], ['london-LND'])
            self.assertNotIn('Cookie', request.headers)
            self.assertNotIn('Authorization', request.headers)
            if query['page'] == ['1']:
                return page_response([
                    posting(title='Older', postDateInGMT='2026-10-01T12:00:00Z'),
                    posting('200000002-2114', 'Part-time', standardWeeklyHours=20)
                ], total=3)
            return page_response([
                posting('PIPE-114438145', 'Full-time and part-time Retail',
                        standardWeeklyHours=38,
                        postDateInGMT='2026-10-10T16:03:15.745116429Z')
            ], total=3, page=2)

        with patch('urllib.request.urlopen', side_effect=apple_http):
            jobs = module.collect()
        self.assertEqual([job['title'] for job in jobs],
                         ['Full-time and part-time Retail', 'Older'])
        self.assertEqual(jobs[0]['url'],
                         'https://jobs.apple.com/en-gb/details/114438145/engineer?team=HRDWR')
        self.assertAlmostEqual(jobs[0]['published'], 1791648195.745116, places=5)
        self.assertEqual(jobs[1]['url'],
                         'https://jobs.apple.com/en-gb/details/200688187-2114/engineer?team=HRDWR')

    def test_rejects_incomplete_changing_duplicate_or_unfiltered_searches(self):
        module = load_script()
        scenarios = [
            [page_response([posting()], total=2), page_response([], total=2, page=2)],
            [page_response([posting()], total=2),
             page_response([posting('200000002-2114')], total=3, page=2)],
            [page_response([posting()], total=2), page_response([posting()], total=2, page=2)],
            [page_response([posting()], total=0)],
            [page_response([], total=-1)],
            [page_response([], total=True)],
            [page_response([posting()], location='postLocation-GBR')],
            [page_response([posting()], page=2)],
            [io.BytesIO(b'<html>Search unavailable</html>')],
            [io.BytesIO(b'<script>window.__staticRouterHydrationData = JSON.parse("{}");</script>')],
        ]
        for responses in scenarios:
            with self.subTest(scenario=scenarios.index(responses)):
                payloads = [response.getvalue() for response in responses]

                def persistently_invalid_search(request, timeout):
                    page = int(parse_qs(urlparse(request.full_url).query)['page'][0])
                    return io.BytesIO(payloads[min(page - 1, len(payloads) - 1)])

                with patch('urllib.request.urlopen', side_effect=persistently_invalid_search):
                    with self.assertRaises(ValueError):
                        module.collect()

    def test_rejects_unusable_job_records_instead_of_printing_misleading_roles(self):
        module = load_script()
        for raw in [None, {}, posting(reqId='../outside'), posting(reqId=None),
                    posting(standardWeeklyHours=None), posting(standardWeeklyHours='35'),
                    posting(standardWeeklyHours=True), posting(standardWeeklyHours=-1),
                    posting(postingTitle=None), posting(transformedPostingTitle='../outside'),
                    posting(team=None), posting(postDateInGMT='not a date'),
                    posting(postDateInGMT='2026-10-09T17:42:37')]:
            with self.subTest(raw=raw):
                with patch('urllib.request.urlopen', return_value=page_response([raw])):
                    with self.assertRaises(ValueError):
                        module.collect()

    def test_confirms_an_empty_apple_response_before_reporting_no_roles(self):
        module = load_script()
        with patch('urllib.request.urlopen', side_effect=[
                page_response([]), page_response([posting()])]):
            jobs = module.collect()
        self.assertEqual([job['title'] for job in jobs], ['Engineer'])

    def test_report_matches_openai_ui_with_the_apple_filter_and_date_labels(self):
        module = load_script()
        spec = importlib.util.spec_from_file_location('reference_ui', SCRIPT.with_name('openai-jobs.py'))
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        now = 1791716400
        ages = [0, 86400, 86401, 259200, 259201, 604800,
                604801, 1209600, 1209601, 2592000, 2592001, -1]
        jobs = [dict(title='Same long engineering title ' * 3,
                     url='https://jobs.apple.com/en-gb/details/' + str(i),
                     published=now - age) for i, age in enumerate(ages)]
        for colour, width in [(False, 88), (True, 40), (False, 24)]:
            with self.subTest(colour=colour, width=width):
                report = module.format_jobs(list(reversed(jobs)), now, colour=colour, width=width)
                expected = reference.format_jobs(list(reversed(jobs)), now, colour=colour, width=width)
                expected = expected.replace('OPENAI CAREERS', 'APPLE CAREERS').replace(
                    textwrap.fill('Full-time / All levels and degrees (feed has no filters)', width=width),
                    textwrap.fill('Full-time (35+ listed weekly hours) / All roles, levels and degrees', width=width)
                ).replace(
                    textwrap.fill('Dates: last published, which may include republication', width=width),
                    textwrap.fill('Dates: Apple posting date / Retail roles may also offer part-time', width=width))
                self.assertEqual(report, expected)
                self.assertIn('10 roles in the last 30 days', report)
                for heading in ['LAST 24 HOURS', '1-3 DAYS AGO', '3-7 DAYS AGO',
                                '7-14 DAYS AGO', '14-30 DAYS AGO']:
                    self.assertIn(heading, report)

    def test_command_prints_empty_report_and_exits_successfully(self):
        result = subprocess.run([sys.executable, '-c', '''
import io, runpy, sys
from unittest.mock import patch
payload = sys.argv[2].encode()
with patch('urllib.request.urlopen', side_effect=lambda *args, **kwargs: io.BytesIO(payload)):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT), page_response([]).getvalue().decode()], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('APPLE CAREERS\nLondon, UK', result.stdout)
        self.assertIn('0 roles in the last 30 days', result.stdout)
        self.assertEqual(result.stdout.count('No roles in this date range.'), 5)
        self.assertNotIn('\033[', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_command_reports_network_failure_without_a_traceback(self):
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
                         'Error: Could not fetch Apple Careers jobs.\nConnection unavailable\n')


if __name__ == '__main__':
    unittest.main()
