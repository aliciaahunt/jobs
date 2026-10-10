import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


SCRIPT = Path(__file__).with_name('mckinsey-jobs.py')


def load_script():
    spec = importlib.util.spec_from_file_location('mckinsey_jobs', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def posting(job_id, title, **changes):
    job = dict(jobID=str(job_id), title=title, friendlyURL='role-' + str(job_id),
               typeOfRole='Full-time', jobOrderCreatedDate='2026-10-09',
               status='Active', hiringLocations=['London (United Kingdom)'])
    job.update(changes)
    return job


class PublicFeed:
    """Serve McKinsey's external search and details contract at the HTTP boundary."""

    def __init__(self, jobs):
        self.jobs = jobs

    def __call__(self, request, timeout):
        url = urlsplit(request.full_url)
        query = parse_qs(url.query)
        if url.path.endswith('/search'):
            size, page = int(query['pageSize'][0]), int(query['start'][0])
            rows = self.jobs[(page - 1) * size:page * size]
            docs = [{key: job[key] for key in ('jobID', 'title', 'friendlyURL')}
                    for job in rows]
            total = len(self.jobs)
        elif url.path.endswith('/details'):
            docs = [job for job in self.jobs
                    if job['friendlyURL'] == query['friendlyUrl'][0]]
            total = len(docs)
        else:
            raise AssertionError('Unexpected public API URL: ' + request.full_url)
        return io.BytesIO(json.dumps(dict(numFound=total, start=0, httpStatus='OK',
                                         statusMessage='Success', docs=docs)).encode())


class McKinseyJobsTests(unittest.TestCase):
    def test_collects_all_active_full_time_london_uk_roles_newest_first(self):
        module = load_script()
        feed = PublicFeed([
            posting(1, 'Associate', jobOrderCreatedDate='2026-09-01'),
            posting(2, 'Multi-office engineer', hiringLocations=[
                'Paris (France)', 'London (United Kingdom)']),
            posting(3, 'Part-time', typeOfRole='Part-time'),
            posting(4, 'Intern', typeOfRole='Internship'),
            posting(5, 'Canadian role', hiringLocations=['London (Canada)']),
            posting(6, 'Closed', status='Inactive'),
            posting(7, 'Other UK city', hiringLocations=['Manchester (United Kingdom)']),
        ])
        with patch('urllib.request.urlopen', side_effect=feed):
            jobs = module.collect()
        self.assertEqual([job['title'] for job in jobs],
                         ['Multi-office engineer', 'Associate'])
        self.assertEqual(jobs[0]['published'], 1791504000)
        self.assertEqual(jobs[0]['url'],
                         'https://www.mckinsey.com/careers/search-jobs/jobs/role-2')

    def test_collects_every_page_of_matching_roles(self):
        module = load_script()
        feed = PublicFeed([posting(i, 'Role ' + str(i)) for i in range(1, 104)])
        with patch('urllib.request.urlopen', side_effect=feed):
            jobs = module.collect()
        self.assertEqual(len(jobs), 103)
        self.assertEqual(jobs[-1]['title'], 'Role 103')

    def test_includes_london_practice_locations(self):
        module = load_script()
        feed = PublicFeed([posting(1, 'AI Engineer', hiringLocations=[
            'Tech & AI/London (United Kingdom)'])])
        with patch('urllib.request.urlopen', side_effect=feed):
            jobs = module.collect()
        self.assertEqual([job['title'] for job in jobs], ['AI Engineer'])

    def test_rejects_incomplete_search_instead_of_reporting_partial_results(self):
        module = load_script()
        source = PublicFeed([posting(1, 'Engineer')])

        def truncated(request, timeout):
            payload = json.load(source(request, timeout))
            if urlsplit(request.full_url).path.endswith('/search'):
                payload['numFound'] = 2
            return io.BytesIO(json.dumps(payload).encode())

        with patch('urllib.request.urlopen', side_effect=truncated):
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                module.collect()

    def test_unrecognised_data_is_reported_as_a_readable_error(self):
        module = load_script()
        for payload in [{}, {'docs': None, 'numFound': 0},
                        {'docs': [], 'numFound': -1},
                        {'docs': [], 'numFound': 0, 'httpStatus': 'ERROR'}]:
            with self.subTest(payload=payload):
                with patch('urllib.request.urlopen', return_value=io.BytesIO(
                        json.dumps(payload).encode())):
                    with self.assertRaisesRegex(ValueError, 'recognised'):
                        module.collect()

    def test_rejects_duplicate_listings(self):
        module = load_script()
        with patch('urllib.request.urlopen', side_effect=PublicFeed([
                posting(1, 'Engineer'), posting(1, 'Engineer')])):
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                module.collect()

    def test_rejects_missing_or_changed_job_details(self):
        module = load_script()
        source = PublicFeed([posting(1, 'Engineer')])
        for changes in [None, {'jobID': '2'}, {'title': ''},
                        {'friendlyURL': 'bad/path'}, {'typeOfRole': None},
                        {'status': None}, {'hiringLocations': None},
                        {'jobOrderCreatedDate': 'not a date'}]:
            with self.subTest(changes=changes):
                def changed(request, timeout):
                    payload = json.load(source(request, timeout))
                    if urlsplit(request.full_url).path.endswith('/details'):
                        if changes is None:
                            payload.update(numFound=0, docs=[])
                        else:
                            payload['docs'][0].update(changes)
                    return io.BytesIO(json.dumps(payload).encode())

                with patch('urllib.request.urlopen', side_effect=changed):
                    with self.assertRaises(ValueError):
                        module.collect()

    def test_report_matches_existing_ui_and_assigns_each_role_once(self):
        module = load_script()
        now = 1791633600
        ages = [0, 86400, 86401, 259200, 259201, 604800,
                604801, 1209600, 1209601, 2592000, 2592001, -1]
        jobs = [dict(title='Role ' + str(i), url='https://example.com/' + str(i),
                     published=now - age) for i, age in enumerate(ages)]
        report = module.format_jobs(list(reversed(jobs)), now)
        self.assertIn('MCKINSEY CAREERS\nLondon, UK', report)
        self.assertIn('Full-time / All roles, levels and degrees', report)
        self.assertIn('job-order creation date', report)
        self.assertIn('10 roles in the last 30 days', report)
        headings = ['LAST 24 HOURS', '1-3 DAYS AGO', '3-7 DAYS AGO',
                    '7-14 DAYS AGO', '14-30 DAYS AGO']
        for i, heading in enumerate(headings):
            section = report.split(heading + '  2 roles')[1]
            if i < 4:
                section = section.split(headings[i + 1])[0]
            for number in (i * 2, i * 2 + 1):
                self.assertIn('• Role ' + str(number) + '  (', section)
                self.assertEqual(report.splitlines().count(
                    '  https://example.com/' + str(number)), 1)
        self.assertNotIn('• Role 10', report)
        self.assertNotIn('• Role 11', report)
        self.assertNotIn('\033[', report)
        self.assertIn('Less than 1 Hour Ago', report)
        self.assertIn('30 Days Ago', report)

    def test_command_runs_without_login_or_browser_and_prints_empty_report(self):
        result = subprocess.run([sys.executable, '-c', '''
import io, runpy, sys
from unittest.mock import patch
payload = b'{"numFound":0,"httpStatus":"OK","docs":[]}'
with patch('urllib.request.urlopen', return_value=io.BytesIO(payload)):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('MCKINSEY CAREERS', result.stdout)
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
                         'Error: Could not fetch McKinsey Careers jobs.\n'
                         'Connection unavailable\n')

    def test_colour_and_narrow_output_preserve_complete_job_links(self):
        module = load_script()
        now = 1791633600
        url = 'https://www.mckinsey.com/careers/search-jobs/jobs/long-role-12345'
        job = dict(title='Senior Engineer working across multiple client teams',
                   url=url, published=now - 7200)
        coloured = module.format_jobs([job], now, colour=True, width=36)
        plain = module.format_jobs([job], now, width=36)
        import re
        self.assertEqual(re.sub(r'\033\[[0-9;]*m', '', coloured), plain)
        self.assertIn('\033[1;31mMCKINSEY CAREERS\033[0m', coloured)
        self.assertIn('  ' + url, plain.splitlines())
        self.assertIn('(2 Hours Ago)', plain)
        for line in plain.splitlines():
            if url not in line:
                self.assertLessEqual(len(line), 36)


if __name__ == '__main__':
    unittest.main()
