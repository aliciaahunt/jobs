import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name('pa-jobs.py')
API = 'https://api.smartrecruiters.com/v1/companies/PAConsulting/postings'


def load_script():
    spec = importlib.util.spec_from_file_location('pa_jobs', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def posting(key, title, **changes):
    result = dict(id=key, name=title, releasedDate='2026-10-09T12:00:00Z',
                  location={'city': 'London', 'country': 'gb'},
                  typeOfEmployment={'id': 'permanent', 'label': 'Full-time'},
                  visibility='PUBLIC', company={'identifier': 'PAConsulting'})
    result.update(changes)
    return result


def page(rows, *, total=None, offset=0, limit=100):
    return dict(content=rows, totalFound=len(rows) if total is None else total,
                offset=offset, limit=limit)


def http(pages):
    def open_url(request, timeout=30):
        return io.BytesIO(json.dumps(pages[request.full_url]).encode())
    return patch('urllib.request.urlopen', side_effect=open_url)


class PAJobsTests(unittest.TestCase):
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
                         'Error: Could not fetch PA Consulting Careers jobs.\nConnection unavailable\n')

    def test_command_prints_empty_report_and_exits_successfully_without_colour(self):
        result = subprocess.run([sys.executable, '-c', '''
import io, runpy, sys
from unittest.mock import patch
payload = b'{"offset":0,"limit":100,"totalFound":0,"content":[]}'
with patch('urllib.request.urlopen', return_value=io.BytesIO(payload)):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PA CONSULTING CAREERS\nLondon, UK\nFull-time / All roles, levels and degrees', result.stdout)
        self.assertIn('0 roles in the last 30 days', result.stdout)
        self.assertEqual(result.stdout.count('No roles in this date range.'), 5)
        self.assertNotIn('\033[', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_report_matches_existing_terminal_ui_and_non_overlapping_date_groups(self):
        spec = importlib.util.spec_from_file_location('openai_jobs', SCRIPT.with_name('openai-jobs.py'))
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        module = load_script()
        now = 1791633600
        ages = [0, 86400, 86401, 259200, 259201, 604800,
                604801, 1209600, 1209601, 2592000, 2592001, -1]
        jobs = [dict(title='Consultant in a team with a long descriptive name ' + str(i),
                     url='https://jobs.smartrecruiters.com/PAConsulting/' + str(100 + i),
                     published=now - age) for i, age in enumerate(ages)]
        for width, colour in [(88, False), (28, False), (88, True)]:
            with self.subTest(width=width, colour=colour):
                expected = reference.format_jobs(list(reversed(jobs)), now, width=width, colour=colour)
                # Compare layout using the same source-specific labels in both reports.
                expected = expected.replace('OPENAI CAREERS', 'PA CONSULTING CAREERS')
                report = module.format_jobs(list(reversed(jobs)), now, width=width, colour=colour)
                import textwrap
                expected = expected.replace(
                    textwrap.fill('Full-time / All levels and degrees (feed has no filters)', width=width),
                    textwrap.fill('Full-time / All roles, levels and degrees', width=width))
                expected = expected.replace(
                    textwrap.fill('Dates: last published, which may include republication', width=width),
                    textwrap.fill('Dates: publication, which may include republication', width=width))
                self.assertEqual(report, expected)
                self.assertIn('10 roles in the last 30 days', report)
                self.assertNotIn('Consultant in a team with a long descriptive name 10', report)

    def test_rejects_invalid_duplicate_or_incomplete_feeds_without_partial_results(self):
        valid = posting('101', 'Consultant')
        cases = [
            {}, {'content': None}, page([valid, valid]), page([{}]),
            page([posting('101', '')]), page([posting('101', 'Analyst', id='')]),
            page([posting('101', 'Analyst', releasedDate='bad date')]),
            page([posting('101', 'Analyst', releasedDate='2026-10-09T12:00:00')]),
            page([posting('101', 'Analyst', location={})]),
            page([posting('101', 'Analyst', company={'identifier': 'OtherCompany'})]),
            page([valid], total=2), page([valid], offset=10), page([valid], total=-1),
        ]
        module = load_script()
        for payload in cases:
            with self.subTest(payload=payload), http({API + '?limit=100&offset=0': payload}):
                with self.assertRaises(ValueError):
                    module.collect()

    def test_collects_london_roles_from_every_page_of_the_feed(self):
        first = [posting(str(i), 'Bristol Analyst',
                         location={'city': 'Bristol', 'country': 'gb'}) for i in range(100)]
        pages = {
            API + '?limit=100&offset=0': page(first, total=102),
            API + '?limit=100&offset=100': page([
                posting('201', 'Director'), posting('202', 'Summer Analyst')], total=102, offset=100),
        }
        with http(pages):
            jobs = load_script().collect()
        self.assertEqual([job['title'] for job in jobs], ['Director', 'Summer Analyst'])

    def test_collects_public_full_time_london_uk_roles_at_every_level_newest_first(self):
        rows = [
            posting('101', 'Graduate Analyst', releasedDate='2026-10-08T12:00:00Z'),
            posting('102', 'Managing Consultant'),
            posting('103', 'Part-time Analyst', typeOfEmployment={'label': 'Part-time'}),
            posting('104', 'Canadian Analyst', location={'city': 'London', 'country': 'ca'}),
            posting('105', 'Bristol Consultant', location={'city': 'Bristol', 'country': 'gb'}),
            posting('106', 'Private Consultant', visibility='INTERNAL'),
            posting('107', 'Contractor', typeOfEmployment={'label': 'Contract'}),
        ]
        module = load_script()
        with http({API + '?limit=100&offset=0': page(rows)}):
            jobs = module.collect()
        self.assertEqual(jobs, [
            {'title': 'Managing Consultant', 'url': 'https://jobs.smartrecruiters.com/PAConsulting/102',
             'published': 1791547200.0},
            {'title': 'Graduate Analyst', 'url': 'https://jobs.smartrecruiters.com/PAConsulting/101',
             'published': 1791460800.0},
        ])


if __name__ == '__main__':
    unittest.main()
