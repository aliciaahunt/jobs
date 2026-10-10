import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse


SCRIPT = Path(__file__).with_name('linkedin-jobs.py')


def load_script(path=SCRIPT):
    spec = importlib.util.spec_from_file_location(path.stem.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def card(job_id, title='Engineer', location='London, England, United Kingdom',
         date='2026-10-08', age='2 days ago', company='LinkedIn',
         company_url='https://www.linkedin.com/company/linkedin'):
    return f'''<li><div class="base-card job-search-card"
      data-entity-urn="urn:li:jobPosting:{job_id}">
      <a class="base-card__full-link" href="https://uk.linkedin.com/jobs/view/engineer-{job_id}?trackingId=abc">Engineer</a>
      <h3 class="base-search-card__title">{title}</h3>
      <h4 class="base-search-card__subtitle"><a href="{company_url}">{company}</a></h4>
      <span class="job-search-card__location">{location}</span>
      <time class="job-search-card__listdate" datetime="{date}">{age}</time>
      </div></li>'''


EMPTY_PAGE = '<!DOCTYPE html>\n<!---->'


class GuestHTTP:
    """LinkedIn responses at the external HTTP boundary, without credentials."""

    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def __call__(self, request, timeout=30):
        self.requests.append(request)
        url = urlparse(request.full_url)
        if url.netloc != 'www.linkedin.com' or url.path != '/jobs-guest/jobs/api/seeMoreJobPostings/search':
            raise AssertionError('Unexpected URL: ' + request.full_url)
        query = parse_qs(url.query, keep_blank_values=True)
        return io.BytesIO(self.pages[int(query['start'][0])].encode())


class LinkedInJobsTests(unittest.TestCase):
    def test_collects_only_linkedins_own_vacancies_using_company_filter_and_employer_identity(self):
        module = load_script()
        http = GuestHTTP({0: card(701, 'LinkedIn Account Director')
                             + card(702, 'LinkedIn Consultant', company='Recruiter',
                                    company_url='https://www.linkedin.com/company/recruiter')
                             + card(703, 'Engineer', company='LinkedIn',
                                    company_url='https://www.linkedin.com/company/lookalike')
                             + card(704, 'Fake employer', company='LinkedIn',
                                    company_url='https://example.com/company/linkedin'),
                          4: EMPTY_PAGE})
        with patch('urllib.request.urlopen', side_effect=http), patch('time.sleep'):
            jobs = module.collect()
        self.assertEqual([job['title'] for job in jobs], ['LinkedIn Account Director'])
        for request in http.requests:
            query = parse_qs(urlparse(request.full_url).query)
            self.assertEqual(query['f_C'], ['1337'])

    def test_command_runs_standalone_and_prints_roles_without_login_or_browser(self):
        result = subprocess.run([sys.executable, '-c', '''
import runpy, sys
from unittest.mock import patch
from test_linkedin_jobs import GuestHTTP, card, EMPTY_PAGE
http = GuestHTTP({0: card(101, 'Engineer', date='2026-10-10', age='2 hours ago'), 1: EMPTY_PAGE})
with patch('urllib.request.urlopen', side_effect=http), patch('time.sleep'), patch('time.time', return_value=1791633600):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], cwd=SCRIPT.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LINKEDIN CAREERS\nLondon, UK', result.stdout)
        self.assertIn('Full-time / All roles at LinkedIn', result.stdout)
        self.assertIn('1 role in the last 30 days', result.stdout)
        self.assertIn('• Engineer  (2 Hours Ago)', result.stdout)
        self.assertIn('  https://www.linkedin.com/jobs/view/101', result.stdout)
        self.assertEqual(result.stdout.count('No roles in this date range.'), 4)
        self.assertEqual(result.stderr, '')

    def test_report_matches_existing_layout_colours_wrapping_and_date_groups(self):
        module = load_script()
        reference = load_script(SCRIPT.with_name('openai-jobs.py'))
        now = 1791633600
        ages = [0, 86400, 86401, 259200, 259201, 604800,
                604801, 1209600, 1209601, 2592000, 2592001, -1]
        jobs = [dict(title='A long role title ' * 10,
                     url='https://www.linkedin.com/jobs/view/' + str(i),
                     published=now - age) for i, age in enumerate(ages)]
        for width in (24, 88, 140):
            for colour in (False, True):
                report = module.format_jobs(jobs, now, width=width, colour=colour)
                original = reference.format_jobs(jobs, now, width=width, colour=colour)
                marker = '10 roles in the last 30 days'
                self.assertEqual(report[report.index(marker):], original[original.index(marker):])
                self.assertIn('LINKEDIN CAREERS', report)
                self.assertIn('London, UK', report)
                self.assertIn('Full-time / All roles at LinkedIn', ' '.join(report.split()))
                self.assertIn('Public results only', report)

    def test_collects_all_guest_pages_with_full_time_london_filters_without_credentials(self):
        module = load_script()
        http = GuestHTTP({0: card(101, 'Older &amp; Co', date='2026-10-01') + card(102, 'Director'),
                          2: card(102, 'Director') + card(103, 'London Canada', 'London, Ontario, Canada')
                             + card(104, 'Nearby', 'Watford, England, United Kingdom')
                             + card(105, 'Graduate', 'Greater London, England, United Kingdom', '2026-10-09'),
                          6: EMPTY_PAGE})
        with patch('urllib.request.urlopen', side_effect=http), patch('time.sleep'):
            jobs = module.collect()
        self.assertEqual([j['title'] for j in jobs], ['Graduate', 'Director', 'Older & Co'])
        self.assertEqual(jobs[0]['published'], 1791504000)
        self.assertEqual(jobs[0]['url'], 'https://www.linkedin.com/jobs/view/105')
        for request in http.requests:
            query = parse_qs(urlparse(request.full_url).query, keep_blank_values=True)
            self.assertEqual(query['location'], ['London, England, United Kingdom'])
            self.assertEqual(query['f_JT'], ['F'])
            self.assertEqual(query['distance'], ['0'])
            self.assertEqual(query['keywords'], [''])
            self.assertEqual(query['f_TPR'], ['r2592000'])
            self.assertNotIn('Cookie', request.headers)
            self.assertNotIn('Authorization', request.headers)

    def test_guest_dates_keep_recent_roles_in_the_last_24_hours(self):
        module = load_script()
        http = GuestHTTP({0: card(201, 'Today', date='2026-10-10', age='2 hours ago')
                             + card(202, 'Yesterday', date='2026-10-09', age='22 hours ago')
                             + card(203, 'Just posted', date='2026-10-10', age='15 minutes ago'),
                          3: EMPTY_PAGE})
        now = 1791633600  # 10 October 2026, 12:00 UTC
        with patch('urllib.request.urlopen', side_effect=http), patch('time.sleep'), patch('time.time', return_value=now):
            jobs = module.collect()
        self.assertEqual([job['published'] for job in jobs],
                         [1791632700, 1791626400, 1791554400])
        report = module.format_jobs(jobs, now)
        self.assertIn('LAST 24 HOURS  3 roles', report)
        self.assertIn('• Yesterday  (22 Hours Ago)', report)

    def test_rejects_blocked_or_unrecognised_responses_instead_of_reporting_no_jobs(self):
        module = load_script()
        for source in ('<html>Sign in to LinkedIn</html>', '<html>Security verification</html>',
                       card(301, date='not a date'), card(301, title=' '),
                       card('bad-id'), card(301).replace('datetime="2026-10-08"', ''),
                       card(301).replace('https://uk.linkedin.com/jobs/view/', 'https://example.com/'),
                       card(301).replace('</div></li>', ''),
                       card(301).replace('job-search-card__location', 'changed-location')):
            with self.subTest(source=source):
                http = GuestHTTP({0: source, 1: EMPTY_PAGE})
                with patch('urllib.request.urlopen', side_effect=http), patch('time.sleep'):
                    with self.assertRaises(ValueError):
                        module.collect()

    def test_recovers_from_a_temporary_guest_rate_limit(self):
        module = load_script()
        http = GuestHTTP({0: card(401), 1: EMPTY_PAGE})
        limited = True

        def interrupted(request, timeout=30):
            nonlocal limited
            if limited:
                limited = False
                raise HTTPError(request.full_url, 429, 'Too Many Requests', {'Retry-After': '1'}, None)
            return http(request, timeout)

        with patch('urllib.request.urlopen', side_effect=interrupted), patch('time.sleep'):
            jobs = module.collect()
        self.assertEqual([j['url'] for j in jobs], ['https://www.linkedin.com/jobs/view/401'])

    def test_rejects_repeated_pages_instead_of_claiming_collection_completed(self):
        module = load_script()
        http = GuestHTTP({0: card(501), 1: card(501), 2: EMPTY_PAGE})
        with patch('urllib.request.urlopen', side_effect=http), patch('time.sleep'):
            with self.assertRaisesRegex(ValueError, 'repeated'):
                module.collect()

    def test_command_explains_persistent_rate_limits_without_printing_partial_results(self):
        result = subprocess.run([sys.executable, '-c', '''
import io, runpy, sys
from unittest.mock import patch
from urllib.error import HTTPError
from test_linkedin_jobs import card
def response(request, timeout=30):
    if request.full_url.endswith('&start=0'):
        return io.BytesIO(card(601).encode())
    raise HTTPError(request.full_url, 429, 'Too Many Requests', {}, None)
with patch('urllib.request.urlopen', side_effect=response), patch('time.sleep'):
    script = sys.argv[1]
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''', str(SCRIPT)], cwd=SCRIPT.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, 'Error: Could not fetch LinkedIn jobs.\n'
                         'LinkedIn temporarily limited guest requests. Please run the script again later.\n')


if __name__ == '__main__':
    unittest.main()
