import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


SCRIPT = Path(__file__).with_name('nvidia-jobs.py')


def load_script(path=SCRIPT):
    spec = importlib.util.spec_from_file_location('jobs_script', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def posting(identifier, title, locations, published=1791504000):
    return dict(id=identifier, name=title, locations=locations,
                standardizedLocations=[], postedTs=published,
                positionUrl=f'/careers/job/{identifier}')


def response(positions, total):
    return io.BytesIO(json.dumps({
        'status': 200,
        'data': {'positions': positions, 'count': total,
                 'appliedFilters': {'timeType': ['full time']}},
    }).encode())


class NvidiaJobsTests(unittest.TestCase):
    def test_report_explains_nvidias_location_search_scope(self):
        report = load_script().format_jobs([], 1791590400)
        self.assertIn('Nearby + UK remote included', report)

    def test_preserves_a_live_listing_returned_by_nvidias_london_full_time_search(self):
        module = load_script()
        # Captured from the public London/full-time search on 10 October 2026.
        job = posting(893398080225,
                      'Senior System Software Engineer, Tegra Security Software',
                      ['UK, Bristol', 'UK, Cambridge'])
        with patch('urllib.request.urlopen', return_value=response([job], 1)):
            jobs = module.collect()
        self.assertEqual(jobs, [{
            'title': 'Senior System Software Engineer, Tegra Security Software',
            'url': 'https://jobs.nvidia.com/careers/job/893398080225',
            'published': 1791504000,
        }])

    def test_command_reports_fetch_failure_without_a_traceback(self):
        result = subprocess.run([sys.executable, '-c', '''
import runpy, sys
from unittest.mock import patch
with patch('urllib.request.urlopen', side_effect=OSError('Connection unavailable')):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr,
                         'Error: Could not fetch NVIDIA Careers jobs.\nConnection unavailable\n')

    def test_command_prints_empty_report_and_exits_successfully(self):
        result = subprocess.run([sys.executable, '-c', '''
import io, runpy, sys
from unittest.mock import patch
payload = b'{"status": 200, "data": {"positions": [], "count": 0, "appliedFilters": {"timeType": ["full time"]}}}'
with patch('urllib.request.urlopen', return_value=io.BytesIO(payload)):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('NVIDIA CAREERS', result.stdout)
        self.assertIn('0 roles in the last 30 days', result.stdout)
        self.assertEqual(result.stdout.count('No roles in this date range.'), 5)
        self.assertNotIn('\033[', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_report_matches_existing_ui_and_non_overlapping_date_groups(self):
        module = load_script()
        reference = load_script(SCRIPT.with_name('openai-jobs.py'))
        now = 1791590400
        ages = [0, 86400, 86401, 259200, 259201, 604800,
                604801, 1209600, 1209601, 2592000, 2592001, -1]
        jobs = [dict(title='Role ' + str(i), url='https://example.com/' + str(i),
                     published=now - age) for i, age in enumerate(ages)]
        report = module.format_jobs(list(reversed(jobs)), now)
        self.assertIn('NVIDIA CAREERS\nLondon, UK\nFull-time / All roles, levels and degrees', report)
        self.assertIn('10 roles in the last 30 days', report)
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
        for colour, width in ((False, 88), (True, 32)):
            with self.subTest(colour=colour, width=width):
                # The established script is the user's reference for layout and colours.
                expected = reference.format_jobs(jobs, now, colour=colour, width=width)
                actual = module.format_jobs(jobs, now, colour=colour, width=width)
                self.assertEqual(actual.split('-' * width, 1)[1],
                                 expected.split('-' * width, 1)[1])

    def test_rejects_unrecognised_feed_or_missing_job_data(self):
        module = load_script()
        job = posting(1, 'London role', ['UK, London'])
        payloads = [{}, {'status': 503, 'data': {
            'positions': [], 'count': 0, 'appliedFilters': {'timeType': ['full time']}}}]
        for changes in ({'name': None}, {'name': ''}, {'postedTs': None},
                        {'postedTs': -1}, {'postedTs': float('nan')},
                        {'positionUrl': ''}, {'locations': None}, {'id': None}):
            payloads.append({'status': 200, 'data': {
                'positions': [dict(job, **changes)], 'count': 1,
                'appliedFilters': {'timeType': ['full time']}}})
        for payload in payloads:
            with self.subTest(payload=payload):
                with patch('urllib.request.urlopen', return_value=io.BytesIO(
                        json.dumps(payload).encode())):
                    with self.assertRaises(ValueError):
                        module.collect()

    def test_rejects_incomplete_changing_or_duplicate_results(self):
        module = load_script()
        job = posting(1, 'London role', ['UK, London'])
        for pages in ([response([job], 2), response([], 2)],
                      [response([job], 2), response([posting(2, 'Other', ['UK, London'])], 3)],
                      [response([job], 2), response([job], 2)],
                      [response([job], 0)], [response([], -1)]):
            with self.subTest(pages=pages):
                with patch('urllib.request.urlopen', side_effect=pages):
                    with self.assertRaises(ValueError):
                        module.collect()

    def test_refuses_results_when_full_time_filter_is_not_confirmed(self):
        module = load_script()
        for filters in ({}, {'timeType': ['part time']}):
            with self.subTest(filters=filters):
                payload = {'status': 200, 'data': {
                    'count': 1, 'positions': [posting(1, 'Role', ['UK, London'])],
                    'appliedFilters': filters}}
                with patch('urllib.request.urlopen', return_value=io.BytesIO(
                        json.dumps(payload).encode())):
                    with self.assertRaisesRegex(ValueError, 'full-time filter'):
                        module.collect()

    def test_collects_every_role_in_nvidias_london_search_across_pages_newest_first(self):
        module = load_script()
        pages = [response([
            posting(1, 'Older London role', ['UK, London'], 1791244800),
            posting(2, 'Nearby Cambridge role', ['UK, Cambridge']),
            posting(3, 'UK remote', ['Remote - UK']),
        ], 6), response([
            posting(4, 'Multilocation London role', ['US, CA, Santa Clara', 'UK, London']),
            dict(posting(5, 'Full-time intern', ['London, UK']),
                 standardizedLocations=['London, England, GB']),
            posting(6, 'Nearby Reading role', ['UK, Reading']),
        ], 6)]
        with patch('urllib.request.urlopen', side_effect=pages) as transport:
            jobs = module.collect()
        self.assertEqual(jobs, [
            {'title': 'Nearby Cambridge role',
             'url': 'https://jobs.nvidia.com/careers/job/2', 'published': 1791504000},
            {'title': 'UK remote',
             'url': 'https://jobs.nvidia.com/careers/job/3', 'published': 1791504000},
            {'title': 'Multilocation London role',
             'url': 'https://jobs.nvidia.com/careers/job/4', 'published': 1791504000},
            {'title': 'Full-time intern',
             'url': 'https://jobs.nvidia.com/careers/job/5', 'published': 1791504000},
            {'title': 'Nearby Reading role',
             'url': 'https://jobs.nvidia.com/careers/job/6', 'published': 1791504000},
            {'title': 'Older London role',
             'url': 'https://jobs.nvidia.com/careers/job/1', 'published': 1791244800},
        ])
        requests = [parse_qs(urlparse(call.args[0].full_url).query)
                    for call in transport.call_args_list]
        self.assertEqual([query['start'] for query in requests], [['0'], ['3']])
        for query in requests:
            self.assertEqual(query['location'], ['London, UK'])
            self.assertEqual(query['filter_time_type'], ['full time'])
            self.assertNotIn('query', query)
            self.assertNotIn('filter_job_type', query)


if __name__ == '__main__':
    unittest.main()
