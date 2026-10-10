import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name('openai-jobs.py')


def load_script():
    spec = importlib.util.spec_from_file_location('openai_jobs', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def posting(title='Engineer', **changes):
    result = dict(title=title, location='London, UK', secondaryLocations=[],
                  employmentType='FullTime', isListed=True,
                  publishedAt='2026-09-05T12:00:00Z',
                  jobUrl='https://jobs.ashbyhq.com/openai/' + str(title))
    result.update(changes)
    return result


def response(jobs):
    return io.BytesIO(json.dumps({'apiVersion': '1', 'jobs': jobs}).encode())


class OpenAIJobsTests(unittest.TestCase):
    def test_collects_listed_full_time_london_roles_newest_first(self):
        module = load_script()
        feed = [posting('Older', publishedAt='2026-09-01T12:00:00+00:00'),
                posting('Multilocation', location='San Francisco',
                        secondaryLocations=[{'location': 'London, UK'}]),
                posting('Contract', employmentType='Contract'),
                posting('Hidden', isListed=False),
                posting('US', location='San Francisco'),
                posting('Canada', location='London, Ontario, Canada')]
        with patch('urllib.request.urlopen', return_value=response(feed)):
            jobs = module.collect()
        self.assertEqual([j['title'] for j in jobs], ['Multilocation', 'Older'])
        self.assertEqual(jobs[0]['published'], 1788609600)
        self.assertEqual(jobs[0]['url'], 'https://jobs.ashbyhq.com/openai/Multilocation')

    def test_rejects_invalid_or_duplicate_feed_instead_of_reporting_partial_results(self):
        module = load_script()
        for payload in [{}, {'jobs': None}, {'jobs': [posting(), posting()]},
                        {'jobs': [posting(publishedAt='not a date')]},
                        {'jobs': [posting(publishedAt='2026-09-05T12:00:00')]},
                        {'jobs': [posting(title=None)]}, {'jobs': [{}]}]:
            with self.subTest(payload=payload):
                with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
                    with self.assertRaises(ValueError):
                        module.collect()

    def test_report_places_each_role_in_its_youngest_date_group(self):
        module = load_script()
        now = 1788696000
        ages = [0, 86400, 86401, 259200, 259201, 604800,
                604801, 1209600, 1209601, 2592000, 2592001, -1]
        jobs = [dict(title='Role ' + str(i), url='https://example.com/' + str(i),
                     published=now - age) for i, age in enumerate(ages)]
        report = module.format_jobs(list(reversed(jobs)), now)
        self.assertIn('OPENAI CAREERS\nLondon, UK', report)
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
        self.assertNotIn('\033[', report)
        self.assertIn('Less than 1 Hour Ago', report)
        self.assertIn('30 Days Ago', report)

    def test_command_prints_empty_report_and_exits_successfully(self):
        result = subprocess.run([sys.executable, '-c', '''
import io, runpy, sys
from unittest.mock import patch
with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"jobs": []}')):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('OPENAI CAREERS', result.stdout)
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
                         'Error: Could not fetch OpenAI Careers jobs.\nConnection unavailable\n')


if __name__ == '__main__':
    unittest.main()
