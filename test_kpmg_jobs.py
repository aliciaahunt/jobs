import html
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


SCRIPT = Path(__file__).with_name('kpmg-jobs.py')


def load_script():
    spec = importlib.util.spec_from_file_location('kpmg_jobs', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def vacancies(roles, pages=1):
    cards = []
    for req, title, employment, capability in roles:
        cards.append(f'''<div class="row vacancy-result" data-vacancy-id="{req}">
          <p class="vacancy-type">Experienced Professional</p>
          <h3>{html.escape(title)}</h3>
          <a class="view-job-description" href="/Vacancies/Role/{req}">View role</a>
        </div>
        <div class="vacancy-description" data-vacancy-id="{req}">
          <p class="vacancy-details-fields"><b>Location:</b> Bristol &amp; Other locations</p>
          <p class="vacancy-details-fields"><b>Capability:</b> {capability}</p>
          <p class="vacancy-details-fields"><b>Employment type:</b> {employment}</p>
        </div>''')
    return ('<div id="vacancy-results-page">' + ''.join(cards) +
            f'<a class="page-link" data-page="{pages}">Last</a></div>')


def portal(roles, total=None):
    total = len(roles) if total is None else total
    rows = ''.join(f'''<tr class="data-row"><td>
      <a class="jobTitle-link" href="/job/Role/{jobid}/">{html.escape(title)}</a>
      <a class="jobTitle-link" href="/job/Role/{jobid}/">{html.escape(title)}</a>
    </td></tr>''' for jobid, title in roles)
    return (f'<table id="searchresults" aria-label="Search results. Page 1, Results 1 to {len(roles)} of {total}">' +
            rows + '</table>')


def detail(req, date):
    return (f'<meta itemprop="datePosted" content="{date}">' +
            '<script>j2w.Apply.init({applyWithLinkedIn2Config:' +
            f'{{"internalId":"{req}-en_GB"}}' + '});</script>')


class KpmgJobsTests(unittest.TestCase):
    def test_collects_exact_full_time_advisory_search_with_portal_posting_date(self):
        module = load_script()
        requested_searches = []

        def respond(request, timeout):
            url = urlsplit(request.full_url)
            if url.netloc == 'www.kpmgcareers.co.uk':
                requested_searches.append(parse_qs(url.query))
                source = vacancies([('109326', 'M&A Manager (Energy)', 'Full Time', 'Advisory'),
                                    ('109444', 'Flexible role', 'Full Time or Part Time', 'Advisory'),
                                    ('101111', 'Tax role', 'Full Time', 'Tax')])
            elif url.path == '/search':
                source = portal([('143001', 'M&A Manager (Energy)')])
            elif url.path == '/job/Role/143001/':
                source = detail('109326', 'Fri Oct 09 02:00:00 UTC 2026')
            else:
                raise AssertionError(request.full_url)
            return io.BytesIO(source.encode())

        with patch('urllib.request.urlopen', side_effect=respond):
            jobs = module.collect()
        self.assertEqual(jobs, [{'id': '109326', 'title': 'M&A Manager (Energy)',
                                'url': 'https://www.kpmgcareers.co.uk/Vacancies/Role/109326',
                                'published': 1791511200}])
        self.assertEqual(requested_searches[0]['officeLocations[0]'], ['London'])
        self.assertEqual(requested_searches[0]['employmentTypes[0]'], ['Full Time'])
        self.assertEqual(requested_searches[0]['capabilities[0]'], ['Advisory'])
        self.assertEqual(requested_searches[0]['intakeType'], ['Experienced'])

    def test_collects_every_page_and_keeps_distinct_requisitions_with_same_title(self):
        module = load_script()

        def respond(request, timeout):
            url = urlsplit(request.full_url)
            query = parse_qs(url.query)
            if url.netloc == 'www.kpmgcareers.co.uk':
                req = '100001' if query.get('page') == ['1'] else '100002'
                source = vacancies([(req, 'Consulting Manager', 'Full Time', 'Advisory')], pages=2)
            elif url.path == '/search':
                rows = [('200001', 'Consulting Manager')]
                if query.get('startrow', ['0']) == ['0']:
                    rows += [(str(300000 + i), 'Other role ' + str(i)) for i in range(24)]
                else:
                    rows = [('200002', 'Consulting Manager')]
                source = portal(rows, total=26)
            elif url.path == '/job/Role/200001/':
                source = detail('100001', 'Thu Oct 08 02:00:00 UTC 2026')
            elif url.path == '/job/Role/200002/':
                source = detail('100002', 'Fri Oct 09 02:00:00 UTC 2026')
            else:
                raise AssertionError(request.full_url)
            return io.BytesIO(source.encode())

        with patch('urllib.request.urlopen', side_effect=respond):
            jobs = module.collect()
        self.assertEqual([job['id'] for job in jobs], ['100002', '100001'])
        self.assertEqual([job['published'] for job in jobs], [1791511200, 1791424800])
        self.assertEqual([job['url'] for job in jobs], [
            'https://www.kpmgcareers.co.uk/Vacancies/Role/100002',
            'https://www.kpmgcareers.co.uk/Vacancies/Role/100001'])

    def test_rejects_unrecognised_incomplete_or_unmatched_results(self):
        module = load_script()
        role = ('109326', 'Manager', 'Full Time', 'Advisory')
        scenarios = [
            ('invalid search', '<html>Service unavailable</html>', portal([('200001', 'Manager')]), detail('109326', 'Fri Oct 09 02:00:00 UTC 2026')),
            ('duplicate vacancy', vacancies([role, role]), portal([('200001', 'Manager')]), detail('109326', 'Fri Oct 09 02:00:00 UTC 2026')),
            ('incomplete portal', vacancies([role]), portal([('200001', 'Manager')], total=2), detail('109326', 'Fri Oct 09 02:00:00 UTC 2026')),
            ('unmatched requisition', vacancies([role]), portal([('200001', 'Manager')]), detail('999999', 'Fri Oct 09 02:00:00 UTC 2026')),
            ('invalid date', vacancies([role]), portal([('200001', 'Manager')]), detail('109326', 'not a date')),
            ('missing date', vacancies([role]), portal([('200001', 'Manager')]), '<html>Login required</html>'),
        ]
        for label, search, listing, job_page in scenarios:
            with self.subTest(label=label):
                def respond(request, timeout):
                    url = urlsplit(request.full_url)
                    source = search if url.netloc == 'www.kpmgcareers.co.uk' else (
                        listing if url.path == '/search' else job_page)
                    return io.BytesIO(source.encode())

                with patch('urllib.request.urlopen', side_effect=respond):
                    with self.assertRaises(ValueError):
                        module.collect()

    def test_report_matches_existing_ui_and_places_roles_in_youngest_date_group(self):
        module = load_script()
        now = 1791655200
        ages = [0, 86400, 86401, 259200, 259201, 604800,
                604801, 1209600, 1209601, 2592000, 2592001, -1]
        jobs = [dict(title='Role ' + str(i), url='https://example.com/' + str(i),
                     published=now - age) for i, age in enumerate(ages)]
        report = module.format_jobs(list(reversed(jobs)), now)
        self.assertIn('KPMG CAREERS\nLondon, UK', report)
        self.assertIn('Full-time / Advisory / Experienced / All roles and levels', report)
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
with patch('urllib.request.urlopen', return_value=io.BytesIO(b'<div id="vacancy-results-page"></div>')):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('KPMG CAREERS', result.stdout)
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
                         'Error: Could not fetch KPMG Careers jobs.\nConnection unavailable\n')


if __name__ == '__main__':
    unittest.main()
