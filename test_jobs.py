"""Run the aggregate CLI with real collectors and simulated external HTTP."""
import contextlib
import io
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from test_last24_jobs import empty_http, NOW


SCRIPT = Path(__file__).with_name('jobs.py')
COMPANIES = ('anthropic', 'apple', 'bain', 'bcg', 'bii', 'deloitte', 'google',
             'kpmg', 'linkedin', 'mckinsey', 'meta', 'nvidia', 'openai', 'pa')
HEADINGS = ('ANTHROPIC CAREERS', 'APPLE CAREERS', 'BAIN CAREERS', 'BCG CAREERS',
            'BRITISH INTERNATIONAL INVESTMENT CAREERS', 'DELOITTE CAREERS',
            'GOOGLE CAREERS', 'KPMG CAREERS', 'LINKEDIN CAREERS', 'MCKINSEY CAREERS',
            'META CAREERS', 'NVIDIA CAREERS', 'OPENAI CAREERS', 'PA CONSULTING CAREERS')
HOSTS = {
    'boards-api.greenhouse.io': 'anthropic', 'jobs.apple.com': 'apple',
    'www.bain.com': 'bain', 'careers.bcg.com': 'bcg',
    'isw.changeworknow.co.uk': 'bii', 'apply.deloitte.co.uk': 'deloitte',
    'www.google.com': 'google', 'www.kpmgcareers.co.uk': 'kpmg',
    'www.linkedin.com': 'linkedin', 'gateway.mckinsey.com': 'mckinsey',
    'www.metacareers.com': 'meta', 'static.xx.fbcdn.net': 'meta',
    'jobs.nvidia.com': 'nvidia', 'api.ashbyhq.com': 'openai',
    'api.smartrecruiters.com': 'pa',
}


def dated_feeds():
    """Three real feed formats with recent, boundary, old and future jobs."""
    import json
    from test_anthropic_jobs import posting as anthropic_posting, response as anthropic_response
    from test_openai_jobs import posting as openai_posting, response as openai_response
    from test_pa_jobs import posting as pa_posting, page
    dates = ['2026-10-10T11:00:00Z', '2026-10-09T12:00:00Z',
             '2026-10-09T11:59:59Z', '2026-10-10T12:00:01Z']
    titles = ['Recent opening', 'Boundary opening', 'Expired opening', 'Future opening']
    anthropic = [anthropic_posting(title, first_published=date) for title, date in zip(titles, dates)]
    openai = [openai_posting(title, publishedAt=date) for title, date in zip(titles, dates)]
    pa = [pa_posting(str(i + 100), title, releasedDate=date)
          for i, (title, date) in enumerate(zip(titles, dates))]
    return {
        'anthropic': lambda *a, **k: anthropic_response(anthropic),
        'openai': lambda *a, **k: openai_response(openai),
        'pa': lambda *a, **k: io.BytesIO(json.dumps(page(pa)).encode()),
    }


class PublicHTTP:
    """Reject requests to companies outside the requested subset."""
    def __init__(self, companies=COMPANIES, responses=None):
        self.responses = {name: empty_http(name) for name in companies}
        self.responses.update(responses or {})

    def __call__(self, request, **kwargs):
        name = HOSTS[urlsplit(request.full_url).hostname]
        if name not in self.responses:
            raise AssertionError('Fetched an unselected company: ' + name)
        return self.responses[name](request, **kwargs)


def command(http, *args, clock=NOW, output=None):
    stdout = output if output is not None else io.StringIO()
    stderr = io.StringIO()
    with patch.object(sys, 'argv', [str(SCRIPT), *args]), \
            patch('urllib.request.urlopen', side_effect=http), \
            patch('time.time', side_effect=clock if callable(clock) else None,
                  return_value=clock), patch('time.sleep'), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            runpy.run_path(str(SCRIPT), run_name='__main__')
        except SystemExit as result:
            status = result.code
    return status, stdout.getvalue(), stderr.getvalue()


class JobsCommandTests(unittest.TestCase):
    def test_apple_encoded_symbol_does_not_fail_the_combined_report(self):
        from test_apple_jobs import posting, page_response
        row = posting('200690117-2114', '\uf8ff Business - Channel Account Executive',
                      transformedPostingTitle='%EF%A3%BF-business-channel-account-executive',
                      team={'teamCode': 'SLDEV'}, postDateInGMT='2026-10-10T11:00:00Z')
        feeds = {'apple': lambda *a, **k: page_response([row]),
                 'openai': dated_feeds()['openai']}
        status, report, error = command(PublicHTTP(('apple', 'openai'), feeds),
                                       '--companies', 'apple', 'openai')
        self.assertEqual(status, 0, error)
        self.assertEqual(error, '')
        self.assertIn(row['postingTitle'], report)
        self.assertIn('https://jobs.apple.com/en-gb/details/200690117-2114/'
                      '%EF%A3%BF-business-channel-account-executive?team=SLDEV', report)
        self.assertIn('OPENAI CAREERS', report)
        self.assertIn('Recent opening', report)

    def test_nvidia_temporary_forbidden_response_recovers_in_the_combined_command(self):
        from test_nvidia_jobs import posting, response
        from urllib.error import HTTPError
        attempts = []
        def feed(request, **kwargs):
            attempts.append(request.full_url)
            if len(attempts) == 1:
                raise HTTPError(request.full_url, 403, 'Forbidden', {}, None)
            return response([posting(1, 'Recent NVIDIA role', ['UK, London'], NOW - 100)], 1)
        status, report, error = command(PublicHTTP(('nvidia',), {'nvidia': feed}),
                                       '--companies', 'nvidia')
        self.assertEqual(status, 0, error)
        self.assertEqual(error, '')
        self.assertIn('Recent NVIDIA role', report)
        self.assertIn('https://jobs.nvidia.com/careers/job/1', report)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])

    def test_nvidia_persistent_forbidden_keeps_other_companies_and_reports_failure(self):
        from urllib.error import HTTPError
        attempts = []
        def feed(request, **kwargs):
            attempts.append(request.full_url)
            raise HTTPError(request.full_url, 403, 'Forbidden', {}, None)
        feeds = {'nvidia': feed, 'openai': dated_feeds()['openai']}
        status, report, error = command(PublicHTTP(('nvidia', 'openai'), feeds),
                                       '--companies', 'nvidia', 'openai')
        self.assertEqual(status, 1)
        self.assertIn('OPENAI CAREERS', report)
        self.assertIn('Recent opening', report)
        self.assertNotIn('NVIDIA CAREERS', report)
        self.assertIn('Could not fetch NVIDIA Careers jobs.', error)
        self.assertIn('HTTP Error 403: Forbidden', error)
        self.assertIn('Request URL: ' + attempts[-1], error)
        self.assertEqual(len(attempts), 3)

    def test_omitting_empty_date_sections_preserves_the_remaining_date_range(self):
        from test_openai_jobs import posting, response
        def feed(*args, **kwargs):
            return response([posting('Five day old opening', publishedAt='2026-10-05T12:00:00Z')])
        status, report, error = command(PublicHTTP(('openai',), {'openai': feed}),
                                       '--companies', 'openai', '--last-30d')
        self.assertEqual(status, 0, error)
        self.assertIn('3-7 DAYS AGO  1 role', report)
        self.assertIn('• Five day old opening', report)
        self.assertNotIn('0-7 DAYS AGO', report)
        self.assertNotIn('0 roles', report)
        self.assertNotIn('LAST 24 HOURS', report)

    def test_nonempty_feeds_with_only_old_or_future_jobs_are_silent(self):
        from test_openai_jobs import posting, response
        dates = [('2026-10-09T11:59:59Z', ()),
                 ('2026-10-10T12:00:01Z', ()),
                 ('2026-09-09T12:00:00Z', ('--last-30d',))]
        for date, args in dates:
            with self.subTest(date=date):
                def feed(*a, **k):
                    return response([posting('Filtered opening', publishedAt=date)])
                status, report, error = command(PublicHTTP(('openai',), {'openai': feed}),
                                               '--companies', 'openai', *args)
                self.assertEqual(status, 0, error)
                self.assertEqual(error, '')
                self.assertEqual(report, '')

    def test_errors_remain_visible_when_all_company_reports_are_empty(self):
        def unavailable(*args, **kwargs):
            raise OSError('Connection unavailable')
        for name in COMPANIES:
            with self.subTest(company=name):
                status, report, error = command(PublicHTTP(responses={name: unavailable}))
                self.assertEqual(status, 1)
                self.assertEqual(report, '')
                self.assertIn('Connection unavailable', error)
                self.assertNotIn('Traceback', error)

    def test_incompatible_date_flags_fail_before_fetching(self):
        def offline(*args, **kwargs):
            self.fail('Conflicting flags must not fetch jobs')
        status, report, error = command(offline, '--last-24h', '--last-30d')
        self.assertEqual(status, 2)
        self.assertEqual(report, '')
        self.assertIn('not allowed', error)

    def test_bain_undated_jobs_are_silent_by_default_but_visible_in_last30(self):
        from test_bain_jobs import posting, search_response, detail_response
        row = posting('100', 'Undated opening')
        def feed(request, **kwargs):
            return (search_response([row]) if '/api/' in request.full_url
                    else detail_response(row, ''))
        for args in [(), ('--last-30d',)]:
            with self.subTest(args=args):
                status, report, error = command(PublicHTTP(('bain',), {'bain': feed}),
                                               '--companies', 'bain', *args)
                self.assertEqual(status, 0, error)
                self.assertEqual(error, '')
                if args:
                    self.assertIn('• Undated opening', report)
                    self.assertIn('DATE UNAVAILABLE  1 role', report)
                    self.assertNotIn('0 roles', report)
                    self.assertNotIn('No roles', report)
                else:
                    self.assertEqual(report, '')

    def test_last30_omits_empty_date_sections_in_nonempty_reports(self):
        feeds = dated_feeds()
        status, report, error = command(PublicHTTP(tuple(feeds), feeds),
                                       '--companies', *feeds, '--last-30d')
        self.assertEqual(status, 0, error)
        self.assertEqual(report.count('3 roles in the last 30 days'), 3)
        self.assertEqual(report.count('1-3 DAYS AGO'), 3)
        self.assertNotIn('0 roles', report)
        self.assertNotIn('No roles', report)
        self.assertNotIn('3-7 DAYS AGO', report)
        self.assertNotIn('7-14 DAYS AGO', report)
        self.assertNotIn('14-30 DAYS AGO', report)

    def test_last30_flag_can_include_older_jobs(self):
        status, report, error = command(PublicHTTP(('openai',),
                                       {'openai': dated_feeds()['openai']}),
                                       '--companies', 'openai', '--last-30d')
        self.assertEqual(status, 0, error)
        self.assertEqual(error, '')
        self.assertIn('3 roles in the last 30 days', report)
        self.assertIn('• Expired opening', report)
        self.assertIn('• Recent opening', report)
        self.assertIn('• Boundary opening', report)
        self.assertNotIn('Future opening', report)

    def test_empty_company_reports_are_silent_for_all_14_companies(self):
        for args in [(), ('--last-24h',), ('--last-30d',)]:
            with self.subTest(args=args):
                status, report, error = command(PublicHTTP(), *args)
                self.assertEqual(status, 0, error)
                self.assertEqual(error, '')
                self.assertEqual(report, '')

    def test_default_subset_shows_only_the_last_24_hours(self):
        selected = ('anthropic', 'openai', 'pa')
        status, report, error = command(PublicHTTP(selected, dated_feeds()),
                                       '--companies', *selected)
        self.assertEqual(status, 0, error)
        self.assertEqual(error, '')
        self.assertEqual(report.count('2 roles in the last 24 hours'), 3)
        self.assertEqual(report.count('• Recent opening'), 3)
        self.assertEqual(report.count('• Boundary opening'), 3)
        self.assertNotIn('Expired opening', report)
        self.assertNotIn('Future opening', report)
        self.assertNotIn('DAYS AGO', report)

    def test_parallel_reports_preserve_terminal_colours(self):
        import os
        class Terminal(io.StringIO):
            def isatty(self):
                return True
            def fileno(self):
                return sys.__stdout__.fileno()
        with patch.dict(os.environ, {'TERM': 'xterm'}):
            os.environ.pop('NO_COLOR', None)
            feeds = dated_feeds()
            status, report, error = command(PublicHTTP(('pa', 'openai'),
                                             {name: feeds[name] for name in ('pa', 'openai')}),
                                             '--companies', 'pa', 'openai', '--last-24h',
                                             output=Terminal())
        self.assertEqual(status, 0, error)
        self.assertIn('\033[1;31mPA CONSULTING CAREERS\033[0m', report)
        self.assertIn('\033[1;31mOPENAI CAREERS\033[0m', report)

    def test_all_14_company_fetches_can_start_before_any_of_them_finishes(self):
        import threading
        barrier = threading.Barrier(14, timeout=5)
        entered = set()
        lock = threading.Lock()
        feeds = PublicHTTP()
        def simultaneous_http(request, **kwargs):
            name = HOSTS[urlsplit(request.full_url).hostname]
            with lock:
                first_request = name not in entered
                entered.add(name)
            if first_request:
                barrier.wait()
            return feeds(request, **kwargs)
        status, report, error = command(simultaneous_http, '--last-24h')
        self.assertEqual(status, 0, error)
        self.assertEqual(error, '')
        self.assertEqual(set(COMPANIES), entered)
        self.assertEqual(report, '')

    def test_unexpected_company_failure_is_flushed_without_waiting_for_other_companies(self):
        import os
        import selectors
        import subprocess
        wrapper = '''
from pathlib import Path
import io, runpy, sys, threading
from unittest.mock import patch
script = sys.argv[1]
sys.path.insert(0, str(Path(script).parent))
from test_jobs import PublicHTTP, NOW, dated_feeds
slow_started = threading.Event()
def slow_feed(*args, **kwargs):
    slow_started.set()
    sys.stdin.readline()
    return dated_feeds()['openai'](*args, **kwargs)
def broken_feed(*args, **kwargs):
    if not slow_started.wait(5):
        raise RuntimeError('The slow company never started')
    raise RuntimeError('Unexpected PA feed failure')
http = PublicHTTP(('openai', 'pa'), {'openai': slow_feed, 'pa': broken_feed})
sys.argv = sys.argv[1:]
with patch('urllib.request.urlopen', side_effect=http), patch('time.time', return_value=NOW):
    runpy.run_path(script, run_name='__main__')
'''
        process = subprocess.Popen([sys.executable, '-c', wrapper, str(SCRIPT),
                                    '--companies', 'openai', 'pa', '--last-24h'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        first = b''
        try:
            with selectors.DefaultSelector() as reader:
                reader.register(process.stderr, selectors.EVENT_READ)
                if reader.select(3):
                    first = os.read(process.stderr.fileno(), 65536)
            self.assertIn(b'Unexpected PA feed failure', first,
                          'Company failures must be printed while other companies are still fetching')
            self.assertIsNone(process.poll())
        finally:
            report, error = process.communicate(input=b'release\n', timeout=10)
        self.assertEqual(process.returncode, 1)
        self.assertIn(b'OPENAI CAREERS', report)
        self.assertNotIn(b'Traceback', first + error)
        self.assertIn(b'pa', (first + error).lower())

    def test_fast_company_report_is_flushed_while_another_company_is_still_fetching(self):
        import os
        import selectors
        import subprocess
        import time
        wrapper = '''
from pathlib import Path
import io, runpy, sys, threading
from unittest.mock import patch
script = sys.argv[1]
sys.path.insert(0, str(Path(script).parent))
from test_jobs import PublicHTTP, NOW, dated_feeds
slow_started = threading.Event()
def slow_feed(*args, **kwargs):
    slow_started.set()
    sys.stdin.readline()
    return dated_feeds()['openai'](*args, **kwargs)
def fast_feed(*args, **kwargs):
    if not slow_started.wait(5):
        raise RuntimeError('The slow company never started')
    return dated_feeds()['pa'](*args, **kwargs)
http = PublicHTTP(('openai', 'pa'), {'openai': slow_feed, 'pa': fast_feed})
sys.argv = sys.argv[1:]
with patch('urllib.request.urlopen', side_effect=http), patch('time.time', return_value=NOW):
    runpy.run_path(script, run_name='__main__')
'''
        # No -u: jobs.py must flush its reports even when stdout is a pipe.
        process = subprocess.Popen([sys.executable, '-c', wrapper, str(SCRIPT),
                                    '--companies', 'openai', 'pa', '--last-24h'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        first = b''
        try:
            deadline = time.monotonic() + 3
            with selectors.DefaultSelector() as reader:
                reader.register(process.stdout, selectors.EVENT_READ)
                while b'Boundary opening' not in first:
                    ready = reader.select(max(0, deadline - time.monotonic()))
                    if not ready:
                        break
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    first += chunk
            self.assertIn(b'PA CONSULTING CAREERS', first,
                          'The fast report must appear before the slow company is released')
            self.assertIn(b'Boundary opening', first)
            self.assertNotIn(b'OPENAI CAREERS', first)
            self.assertIsNone(process.poll(), 'The slow company should still be fetching')
        finally:
            remaining, error = process.communicate(input=b'release\n', timeout=10)
        self.assertEqual(process.returncode, 0, error.decode())
        self.assertEqual(error, b'')
        self.assertIn(b'OPENAI CAREERS', remaining)
        self.assertEqual((first + remaining).count(b'PA CONSULTING CAREERS'), 1)

    def test_python_command_loads_selected_company_scripts_from_another_working_directory(self):
        import subprocess
        import tempfile
        wrapper = '''
from pathlib import Path
import runpy, sys
from unittest.mock import patch
script = sys.argv[1]
sys.path.insert(0, str(Path(script).parent))
from test_jobs import PublicHTTP, NOW, dated_feeds
sys.argv = sys.argv[1:]
http = PublicHTTP(('pa',), {'pa': dated_feeds()['pa']})
with patch('urllib.request.urlopen', side_effect=http), patch('time.time', return_value=NOW):
    runpy.run_path(script, run_name='__main__')
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c', wrapper, str(SCRIPT),
                                     '--companies', 'pa', '--last-24h'],
                                    cwd=directory, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertIn('PA CONSULTING CAREERS', result.stdout)
        self.assertIn('2 roles in the last 24 hours', result.stdout)
        self.assertEqual(result.stdout.count('CAREERS'), 1)

    def test_last24_without_a_company_list_applies_to_all_14_companies(self):
        status, report, error = command(PublicHTTP(responses=dated_feeds()), '--last-24h')
        self.assertEqual(status, 0, error)
        self.assertEqual(error, '')
        for name, heading in zip(COMPANIES, HEADINGS):
            self.assertEqual(report.count(heading), 1 if name in dated_feeds() else 0)
        self.assertEqual(report.count('2 roles in the last 24 hours'), 3)
        self.assertEqual(report.count('LAST 24 HOURS'), 3)
        self.assertNotIn('0 roles', report)
        self.assertNotIn('DAYS AGO', report)
        self.assertNotIn('30 days', report)

    def test_invalid_or_empty_company_lists_are_rejected_before_fetching_jobs(self):
        for args in [('--companies', 'unknown'), ('--companies',),
                     ('--companies', 'pa', 'unknown', '--last-24h')]:
            with self.subTest(args=args):
                def offline(*a, **k):
                    self.fail('Invalid company selection must not fetch jobs')
                status, report, error = command(offline, *args)
                self.assertEqual(status, 2)
                self.assertEqual(report, '')
                self.assertIn('--companies', error)

    def test_help_describes_default_window_and_company_names_without_fetching_jobs(self):
        def offline(*args, **kwargs):
            self.fail('Help must not fetch jobs')
        status, report, error = command(offline, '--help')
        self.assertEqual(status, 0, error)
        self.assertEqual(error, '')
        self.assertIn('--last-24h', report)
        self.assertIn('--last-30d', report)
        self.assertIn('24 hours (default)', report)
        self.assertIn('--companies', report)
        for name in COMPANIES:
            self.assertIn(name, report)

    def test_selected_reports_match_the_existing_individual_commands(self):
        import re
        from test_last24_jobs import run_command
        feeds = dated_feeds()
        for args in [(), ('--last-24h',)]:
            with self.subTest(args=args):
                status, report, error = command(PublicHTTP(tuple(feeds), feeds),
                                               '--companies', *feeds, *args)
                self.assertEqual(status, 0, error)
                individual = []
                for name, feed in feeds.items():
                    code, output, errors = run_command(name, feed, '--last-24h')
                    self.assertEqual(code, 0, errors)
                    individual.append(output)
                heading = '|'.join(re.escape(value) for value in HEADINGS)
                sections = re.split(r'(?m)(?=^(?:' + heading + r')$)', report)
                self.assertCountEqual([section.strip() for section in sections if section.strip()],
                                      [output.strip() for output in individual])

    def test_repeated_company_names_run_each_company_once(self):
        feeds = dated_feeds()
        status, report, error = command(PublicHTTP(('openai', 'pa'),
                                         {name: feeds[name] for name in ('openai', 'pa')}),
                                         '--companies', 'openai', 'pa', 'openai', '--last-24h')
        self.assertEqual(status, 0, error)
        self.assertEqual(report.count('OPENAI CAREERS'), 1)
        self.assertEqual(report.count('PA CONSULTING CAREERS'), 1)
        self.assertEqual(report.count('2 roles in the last 24 hours'), 2)

    def test_reports_other_selected_companies_and_exits_unsuccessfully_when_one_feed_fails(self):
        def unavailable(*args, **kwargs):
            raise OSError('OpenAI connection unavailable')
        status, report, error = command(PublicHTTP(('openai', 'anthropic', 'pa'),
                                         {**dated_feeds(), 'openai': unavailable}),
                                         '--companies', 'openai', 'anthropic', 'pa', '--last-24h')
        self.assertEqual(status, 1)
        self.assertIn('OpenAI connection unavailable', error)
        self.assertNotIn('Traceback', error)
        self.assertNotIn('OPENAI CAREERS', report)
        self.assertIn('ANTHROPIC CAREERS', report)
        self.assertIn('PA CONSULTING CAREERS', report)
        self.assertEqual(report.count('2 roles in the last 24 hours'), 2)

    def test_last24_uses_the_same_cutoff_for_all_companies_even_when_fetching_takes_time(self):
        tick = iter((NOW, NOW + 10, NOW + 20))
        status, report, error = command(PublicHTTP(('anthropic', 'openai', 'pa'), dated_feeds()),
                                         '--last-24h', '--companies', 'anthropic', 'openai', 'pa',
                                         clock=lambda: next(tick))
        self.assertEqual(status, 0, error)
        self.assertEqual(report.count('• Boundary opening'), 3)
        self.assertEqual(report.count('2 roles in the last 24 hours'), 3)
        updated = [line for line in report.splitlines() if ' / Updated ' in line]
        self.assertEqual(len(updated), 3)
        self.assertEqual(len(set(updated)), 1)

    def test_last24_applies_to_every_selected_company_without_fetching_other_companies(self):
        selected = ('openai', 'anthropic', 'pa')
        status, report, error = command(PublicHTTP(selected, dated_feeds()),
                                         '--companies', *selected, '--last-24h')
        self.assertEqual(status, 0, error)
        self.assertEqual(error, '')
        self.assertEqual(report.count('2 roles in the last 24 hours'), 3)
        self.assertEqual(report.count('• Boundary opening'), 3)
        self.assertEqual(report.count('• Recent opening'), 3)
        self.assertNotIn('Expired opening', report)
        self.assertNotIn('Future opening', report)
        self.assertNotIn('DAYS AGO', report)
        self.assertNotIn('30 days', report)

    def test_company_list_accepts_arbitrary_lengths_and_fetches_only_selected_companies(self):
        for size in (1, 2, 3, 5, 14):
            selected = list(reversed(COMPANIES))[:size]
            with self.subTest(companies=selected):
                fetched = set()
                feeds = PublicHTTP(selected)
                def record_http(request, **kwargs):
                    fetched.add(HOSTS[urlsplit(request.full_url).hostname])
                    return feeds(request, **kwargs)
                status, report, error = command(record_http, '--companies', *selected)
                self.assertEqual(status, 0, error)
                self.assertEqual(error, '')
                self.assertEqual(fetched, set(selected))
                self.assertEqual(report, '')

    def test_default_command_omits_empty_companies_and_shows_recent_jobs(self):
        feeds = dated_feeds()
        status, report, error = command(PublicHTTP(responses=feeds))
        self.assertEqual(status, 0, error)
        self.assertEqual(error, '')
        for name, heading in zip(COMPANIES, HEADINGS):
            self.assertEqual(report.count(heading), 1 if name in feeds else 0)
        self.assertEqual(report.count('2 roles in the last 24 hours'), 3)
        self.assertEqual(report.count('LAST 24 HOURS'), 3)
        self.assertNotIn('0 roles', report)
        self.assertNotIn('No roles', report)
        self.assertNotIn('DAYS AGO', report)
        self.assertNotIn('Expired opening', report)
        self.assertNotIn('Future opening', report)
        self.assertFalse(report.startswith('\n'))
        self.assertNotIn('\033[', report)


if __name__ == '__main__':
    unittest.main()
