import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name('bcg-jobs.py')


def load_script():
    spec = importlib.util.spec_from_file_location('bcg_jobs', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def posting(title='Engineer', **changes):
    raw = dict(title=title, jobSeqNo='BCG1US' + str(title).replace(' ', ''),
               city='London', country='United Kingdom', type='Full-Time',
               multi_location=['London, United Kingdom'],
               postedDate='2026-10-09T00:00:00.000+0000')
    raw.update(changes)
    return raw


def response(rows, total=None):
    payload = {'refineSearch': {'status': 200, 'hits': len(rows),
                               'totalHits': len(rows) if total is None else total,
                               'data': {'jobs': rows}}}
    return io.BytesIO(json.dumps(payload).encode())


class BCGJobsTests(unittest.TestCase):
    def test_collects_every_page_of_full_time_london_roles_newest_first(self):
        module = load_script()
        batches = [response([
            posting('Older', postedDate='2026-10-01T00:00:00.000+0000'),
            posting('Secondary', city='Boston', country='United States of America',
                    multi_location=['Boston, Massachusetts, United States of America',
                                    'London, United Kingdom'])], total=5),
            response([posting('Part time', type='Part-Time'),
                      posting('Canada', country='Canada',
                              multi_location=['London, Ontario, Canada']),
                      posting('Newest', postedDate='2026-10-10T00:00:00.000+0000')], total=5)]
        with patch('urllib.request.urlopen', side_effect=batches) as network:
            jobs = module.collect()
        self.assertEqual([j['title'] for j in jobs], ['Newest', 'Secondary', 'Older'])
        self.assertEqual(jobs[0]['published'], 1791590400)
        self.assertEqual(jobs[0]['url'],
                         'https://careers.bcg.com/global/en/job/BCG1USNewest')
        requests = [json.loads(call.args[0].data) for call in network.call_args_list]
        self.assertEqual([r['from'] for r in requests], [0, 2])
        for request in requests:
            self.assertEqual(request['selected_fields'], {
                'city': ['London'], 'country': ['United Kingdom'], 'type': ['Full-Time']})
            self.assertEqual(request['keywords'], '')

    def test_rejects_incomplete_changing_or_duplicate_search_results(self):
        module = load_script()
        feeds = [
            [response([posting()], total=2), response([], total=2)],
            [response([posting()], total=2), response([posting('Other')], total=3)],
            [response([posting(), posting()])],
            [response([posting()], total=0)],
            [io.BytesIO(b'{}')],
            [io.BytesIO(b'{"refineSearch":{"status":500}}')],
        ]
        for feed in feeds:
            with self.subTest(feed=feed), patch('urllib.request.urlopen', side_effect=feed):
                with self.assertRaises(ValueError):
                    module.collect()

    def test_report_matches_existing_ui_and_non_overlapping_date_groups(self):
        module = load_script()
        now = 1791590400
        ages = [0, 86400, 86401, 259200, 259201, 604800,
                604801, 1209600, 1209601, 2592000, 2592001, -1]
        jobs = [dict(title='Role ' + str(i), url='https://example.com/' + str(i),
                     published=now - age) for i, age in enumerate(ages)]
        report = module.format_jobs(list(reversed(jobs)), now)
        self.assertIn('BCG CAREERS\nLondon, UK', report)
        self.assertIn('Full-time / All roles, levels and degrees', report)
        self.assertIn('10 roles in the last 30 days', report)
        self.assertIn('12 matched / Updated', report)
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

    def test_rejects_missing_or_invalid_job_metadata(self):
        module = load_script()
        invalid = [posting(title=None), posting(title=''), posting(jobSeqNo='../login'),
                   posting(postedDate='2026-10-09T00:00:00.000'),
                   posting(postedDate='not a date'), posting(type=None),
                   posting(multi_location='London, United Kingdom'),
                   posting(city=None), posting(country=None), {}]
        for raw in invalid:
            with self.subTest(raw=raw), patch('urllib.request.urlopen',
                                             return_value=response([raw])):
                with self.assertRaises(ValueError):
                    module.collect()

    def test_command_prints_the_report_without_login_or_browser(self):
        module = load_script()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('urllib.request.urlopen', return_value=response([posting()])), \
                patch('sys.stdout', stdout), patch('sys.stderr', stderr):
            code = module.main([])
        self.assertEqual(code, 0)
        self.assertIn('BCG CAREERS\nLondon, UK', stdout.getvalue())
        self.assertIn('1 matched / Updated', stdout.getvalue())
        self.assertEqual(stderr.getvalue(), '')
        self.assertNotIn('\033[', stdout.getvalue())

    def test_command_reports_malformed_utf8_without_a_traceback(self):
        module = load_script()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'\xff')), \
                patch('sys.stdout', stdout), patch('sys.stderr', stderr):
            code = module.main([])
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertTrue(stderr.getvalue().startswith('Error: Could not fetch BCG Careers jobs.\n'))
        self.assertNotIn('Traceback', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
