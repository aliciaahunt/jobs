"""Exercise the real command line with fixed time and external HTTP responses."""
import contextlib
import io
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch

NOW = 1791633600  # 10 October 2026, 12:00 UTC
DATES = ['2026-10-10T11:00:00Z', '2026-10-09T12:00:00Z',
         '2026-10-09T11:59:59Z', '2026-10-10T12:00:01Z']
TITLES = ['Recent opening', 'Boundary opening', 'Expired opening', 'Future opening']


def empty_http(name):
    """Empty but valid source responses; no production collector is replaced."""
    if name in ('openai', 'anthropic'):
        return lambda *a, **k: io.BytesIO(b'{"jobs": []}')
    if name == 'apple':
        from test_apple_jobs import page_response
        return lambda *a, **k: page_response([])
    if name == 'bain':
        from test_bain_jobs import search_response
        return lambda *a, **k: search_response([])
    if name == 'bcg':
        from test_bcg_jobs import response
        return lambda *a, **k: response([])
    if name == 'bii':
        from test_bii_jobs import feed, search
        return lambda request, **k: io.BytesIO(
            (feed() if request.full_url.endswith('.rss') else search()).encode())
    if name == 'deloitte':
        from test_deloitte_jobs import listing
        return lambda *a, **k: io.BytesIO(listing([], '0 results'))
    if name == 'google':
        return lambda *a, **k: io.BytesIO(b'<script>AF_initDataCallback({data:[null,null,0,20]});</script>')
    if name == 'kpmg':
        return lambda *a, **k: io.BytesIO(b'<div id="vacancy-results-page"></div>')
    if name == 'linkedin':
        from test_linkedin_jobs import GuestHTTP, EMPTY_PAGE
        return GuestHTTP({0: EMPTY_PAGE})
    if name == 'mckinsey':
        from test_mckinsey_jobs import PublicFeed
        return PublicFeed([])
    if name == 'meta':
        from test_meta_jobs import MetaHTTP
        return MetaHTTP([])
    if name == 'nvidia':
        from test_nvidia_jobs import response
        return lambda *a, **k: response([], 0)
    if name == 'pa':
        return lambda *a, **k: io.BytesIO(b'{"content":[],"offset":0,"limit":100,"totalFound":0}')
    raise AssertionError('Missing empty HTTP fixture for ' + name)


def run_command(name, http, *args, now=NOW):
    script = Path(__file__).with_name(name + '-jobs.py')
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.object(sys, 'argv', [str(script), *args]), \
            patch('urllib.request.urlopen', side_effect=http), \
            patch('time.time', return_value=now), patch('time.sleep'), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            runpy.run_path(str(script), run_name='__main__')
        except SystemExit as result:
            status = result.code
    return status, stdout.getvalue(), stderr.getvalue()


class Last24HoursCommandTests(unittest.TestCase):
    def test_bcg_last24_collects_more_than_100_jobs_from_one_consistent_search_response(self):
        import json
        from test_bcg_jobs import posting, response
        rows = [posting(TITLES[0], postedDate='2026-10-10T11:00:00.000+0000'),
                posting(TITLES[1], postedDate='2026-10-09T12:00:00.000+0000')]
        rows.extend(posting('Old vacancy ' + str(i), postedDate='2026-10-01T00:00:00.000+0000')
                    for i in range(102))
        def http(request, **kwargs):
            query = json.loads(request.data)
            start, size = query['from'], query['size']
            batch = rows[start:start + size]
            # Relevance ordering can move a listing between two requests.
            if start:
                batch[-1] = rows[0]
            return response(batch, total=104)
        self.assert_recent_report('bcg', http)

    def test_apple_last24_stops_on_the_page_crossing_the_cutoff(self):
        from test_apple_jobs import posting, page_response
        from urllib.parse import parse_qs, urlsplit
        def http(request, **kwargs):
            self.assertEqual(parse_qs(urlsplit(request.full_url).query)['page'], ['1'],
                             'No older pages are needed once the date-sorted page crosses the cutoff')
            return page_response([posting('100', TITLES[0], postDateInGMT=DATES[0]),
                                  posting('101', TITLES[1], postDateInGMT=DATES[1]),
                                  posting('102', TITLES[2], postDateInGMT=DATES[2])], total=6)
        self.assert_recent_report('apple', http)

    def test_nvidia_last24_stops_on_the_page_crossing_the_cutoff(self):
        from test_nvidia_jobs import posting, response
        from urllib.parse import parse_qs, urlsplit
        def http(request, **kwargs):
            self.assertEqual(parse_qs(urlsplit(request.full_url).query)['start'], ['0'],
                             'No older pages are needed once the date-sorted page crosses the cutoff')
            return response([posting(100, TITLES[0], ['London, UK'], 1791630000),
                             posting(101, TITLES[1], ['London, UK'], 1791547200),
                             posting(102, TITLES[2], ['London, UK'], 1791547199)], 6)
        self.assert_recent_report('nvidia', http)

    def test_every_script_last24_prints_one_empty_group_when_no_jobs_match(self):
        for script in sorted(Path(__file__).parent.glob('*-jobs.py')):
            name = script.stem[:-5]
            with self.subTest(script=script.name):
                status, report, error = run_command(name, empty_http(name), '--last-24h')
                self.assertEqual(status, 0, error)
                self.assertEqual(error, '')
                self.assertIn('0 roles in the last 24 hours', report)
                self.assertIn('0 matched / Updated', report)
                self.assertEqual(report.count('No roles in this date range.'), 1)
                self.assertNotIn('DAYS AGO', report)

    def test_every_script_last24_reports_network_failure_without_partial_results(self):
        for script in sorted(Path(__file__).parent.glob('*-jobs.py')):
            with self.subTest(script=script.name):
                status, report, error = run_command(script.stem[:-5], OSError('Connection unavailable'), '--last-24h')
                self.assertEqual(status, 1)
                self.assertEqual(report, '')
                self.assertIn('Connection unavailable', error)
                self.assertNotIn('Traceback', error)

    def test_bain_last24_explains_why_undated_roles_are_excluded(self):
        from test_bain_jobs import search_response
        status, report, error = run_command('bain', lambda *a, **k: search_response([]), '--last-24h')
        self.assertEqual(status, 0, error)
        self.assertIn('undated roles excluded', report)
        self.assertNotIn('undated roles shown separately', report)

    def test_every_script_help_describes_last24_without_fetching_jobs(self):
        for script in sorted(Path(__file__).parent.glob('*-jobs.py')):
            with self.subTest(script=script.name):
                def offline(*args, **kwargs):
                    self.fail('Help must not fetch jobs')
                status, report, error = run_command(script.stem[:-5], offline, '--help')
                self.assertEqual(status, 0, error)
                self.assertIn('--last-24h', report)
                self.assertIn('last 24 hours', report)

    def test_apple_last24_stops_paging_after_old_jobs_including_part_time_jobs(self):
        from test_apple_jobs import posting, page_response
        from urllib.parse import parse_qs, urlsplit
        def http(request, **kwargs):
            page = int(parse_qs(urlsplit(request.full_url).query)['page'][0])
            if page == 1:
                return page_response([posting('100', TITLES[0], postDateInGMT=DATES[0]),
                                      posting('101', TITLES[1], postDateInGMT=DATES[1])], total=6)
            self.assertEqual(page, 2, 'Unnecessary old search page')
            return page_response([posting('102', TITLES[2], postDateInGMT=DATES[2]),
                                  posting('103', 'Old part-time opening',
                                          postDateInGMT='2026-10-08T00:00:00Z',
                                          standardWeeklyHours=20)], total=6, page=2)
        self.assert_recent_report('apple', http)

    def test_nvidia_last24_stops_paging_after_the_first_page_of_old_jobs(self):
        from test_nvidia_jobs import posting, response
        from urllib.parse import parse_qs, urlsplit
        def http(request, **kwargs):
            start = parse_qs(urlsplit(request.full_url).query)['start'][0]
            if start == '0':
                return response([posting(100, 'Recent opening', ['London, UK'], 1791630000),
                                 posting(101, 'Boundary opening', ['London, UK'], 1791547200)], 6)
            self.assertEqual(start, '2', 'Unnecessary old search page')
            return response([posting(102, 'Expired opening', ['London, UK'], 1791547199),
                             posting(103, 'Older opening', ['London, UK'], 1791460800)], 6)
        self.assert_recent_report('nvidia', http)

    def test_linkedin_last24_requests_only_the_24_hour_search_from_the_server(self):
        from test_linkedin_jobs import card, EMPTY_PAGE
        from urllib.parse import parse_qs, urlsplit
        def http(request, **kwargs):
            query = parse_qs(urlsplit(request.full_url).query)
            self.assertEqual(query['f_TPR'], ['r86400'])
            self.assertEqual(query['f_C'], ['1337'])
            body = card(100, 'Recent opening', age='1 hour ago') if query['start'] == ['0'] else EMPTY_PAGE
            return io.BytesIO(body.encode())
        status, report, error = run_command('linkedin', http, '--last-24h')
        self.assertEqual(status, 0, error)
        self.assertIn('1 role in the last 24 hours', report)

    def test_bii_last24_skips_detail_requests_for_known_old_and_future_vacancies(self):
        from test_bii_jobs import listing, search, detail, feed, BASE
        pages = {
            BASE + 'search.rss': feed(('100', 'Recent opening', 'Sat, 10 Oct 2026 11:00:00 GMT'),
                                      ('101', 'Expired opening', 'Fri, 09 Oct 2026 11:59:59 GMT'),
                                      ('102', 'Future opening', 'Sun, 11 Oct 2026 00:00:00 GMT')),
            BASE + 'search/new': search(listing('100', 'Recent opening'),
                                        listing('101', 'Expired opening'),
                                        listing('102', 'Future opening')),
            BASE + 'positions/100': detail('Recent opening'),
        }
        def http(request, **kwargs):
            self.assertIn(request.full_url, pages, 'Unnecessary vacancy detail request')
            return io.BytesIO(pages[request.full_url].encode())
        status, report, error = run_command('bii', http, '--last-24h')
        self.assertEqual(status, 0, error)
        self.assertIn('1 role in the last 24 hours', report)
        self.assertIn('Recent opening', report)

    def assert_recent_report(self, name, http, now=NOW):
        status, report, error = run_command(name, http, '--last-24h', now=now)
        self.assertEqual(status, 0, error)
        self.assertEqual(error, '')
        self.assertIn('2 roles in the last 24 hours', report)
        self.assertIn('2 matched / Updated', report)
        self.assertIn('LAST 24 HOURS  2 roles', report)
        for title in TITLES[:2]:
            self.assertIn(title, report)
        for title in TITLES[2:]:
            self.assertNotIn(title, report)
        for unwanted in ('1-3 DAYS AGO', '3-7 DAYS AGO', '7-14 DAYS AGO',
                         '14-30 DAYS AGO', '30 days', 'DATE UNAVAILABLE', 'Undated opening'):
            self.assertNotIn(unwanted, report)

    def test_openai_last24_command_includes_cutoff_and_excludes_old_and_future_jobs(self):
        from test_openai_jobs import posting, response
        rows = [posting(title, publishedAt=date) for title, date in zip(TITLES, DATES)]
        self.assert_recent_report('openai', lambda *a, **k: response(rows))

    def test_anthropic_last24_command_includes_cutoff_and_excludes_old_and_future_jobs(self):
        from test_anthropic_jobs import posting, response
        rows = [posting(title, first_published=date) for title, date in zip(TITLES, DATES)]
        self.assert_recent_report('anthropic', lambda *a, **k: response(rows))

    def test_apple_last24_command_includes_cutoff_and_excludes_old_and_future_jobs(self):
        from test_apple_jobs import posting, page_response
        rows = [posting(str(i + 100), title, postDateInGMT=date)
                for i, (title, date) in enumerate(zip(TITLES, DATES))]
        self.assert_recent_report('apple', lambda *a, **k: page_response(rows))

    def test_pa_last24_command_includes_cutoff_and_excludes_old_and_future_jobs(self):
        import json
        from test_pa_jobs import posting, page
        rows = [posting(str(i + 100), title, releasedDate=date)
                for i, (title, date) in enumerate(zip(TITLES, DATES))]
        self.assert_recent_report('pa', lambda *a, **k: io.BytesIO(json.dumps(page(rows)).encode()))

    def test_nvidia_last24_command_includes_cutoff_and_excludes_old_and_future_jobs(self):
        from test_nvidia_jobs import posting, response
        rows = [posting(i + 100, title, ['London, UK'], published=stamp)
                for i, (title, stamp) in enumerate(zip(TITLES, [1791630000, 1791547200,
                                                               1791547199, 1791633601]))]
        self.assert_recent_report('nvidia', lambda *a, **k: response(rows, len(rows)))

    def test_bcg_last24_command_includes_cutoff_and_excludes_old_and_future_jobs(self):
        from test_bcg_jobs import posting, response
        dates = ['2026-10-10T13:00:00.000+0200', '2026-10-09T12:00:00.000+0000',
                 '2026-10-09T11:59:59.000+0000', '2026-10-10T12:00:01.000+0000']
        rows = [posting(title, postedDate=date) for title, date in zip(TITLES, dates)]
        self.assert_recent_report('bcg', lambda *a, **k: response(rows))

    def test_meta_last24_command_includes_cutoff_and_excludes_old_and_future_jobs(self):
        from test_meta_jobs import posting, detail, MetaHTTP
        rows = [posting(i + 100, title) for i, title in enumerate(TITLES)]
        pages = {str(i + 100): detail(title, date=date)
                 for i, (title, date) in enumerate(zip(TITLES, DATES))}
        self.assert_recent_report('meta', MetaHTTP(rows, pages))

    def test_linkedin_last24_command_includes_cutoff_and_excludes_old_and_future_jobs(self):
        from test_linkedin_jobs import card, GuestHTTP, EMPTY_PAGE
        rows = [card(i + 100, title, date=date[:10], age=age)
                for i, (title, date, age) in enumerate(zip(
                    TITLES, DATES, ['1 hour ago', '24 hours ago', '2 days ago', 'Just posted']))]
        # Future date uses tomorrow because LinkedIn's structured dates have day precision.
        rows[-1] = card(103, TITLES[-1], date='2026-10-11', age='Just posted')
        self.assert_recent_report('linkedin', GuestHTTP({0: ''.join(rows), 4: EMPTY_PAGE}))

    def test_mckinsey_last24_command_respects_day_precision_and_cutoff(self):
        from test_mckinsey_jobs import posting, PublicFeed
        dates = ['2026-10-10', '2026-10-09', '2026-10-08', '2026-10-11']
        rows = [posting(i + 100, title, jobOrderCreatedDate=date)
                for i, (title, date) in enumerate(zip(TITLES, dates))]
        self.assert_recent_report('mckinsey', PublicFeed(rows), now=1791590400)

    def test_bain_last24_command_omits_undated_roles_and_respects_day_precision(self):
        from test_bain_jobs import posting, search_response, detail_response
        from urllib.parse import parse_qs, urlsplit
        dates = ['2026-10-10', '2026-10-09', '2026-10-08', '2026-10-11', '']
        rows = [posting(str(i + 100), title)
                for i, title in enumerate([*TITLES, 'Undated opening'])]
        def http(request, **kwargs):
            if '/api/' in request.full_url:
                return search_response(rows)
            index = int(parse_qs(urlsplit(request.full_url).query)['jobid'][0]) - 100
            return detail_response(rows[index], dates[index])
        self.assert_recent_report('bain', http, now=1791590400)

    def test_deloitte_last24_command_uses_uk_midnight_dates(self):
        from test_deloitte_jobs import listing
        dates = ['10 Oct 2026', '09 Oct 2026', '08 Oct 2026', '11 Oct 2026']
        rows = [(str(i + 100), title, 'London', date)
                for i, (title, date) in enumerate(zip(TITLES, dates))]
        self.assert_recent_report('deloitte', lambda *a, **k: io.BytesIO(
            listing(rows, '1-4 of 4 results')), now=1791586800)

    def test_bii_last24_command_includes_cutoff_and_excludes_old_and_future_jobs(self):
        from test_bii_jobs import listing, search, detail, feed, BASE
        dates = ['Sat, 10 Oct 2026 11:00:00 GMT', 'Fri, 09 Oct 2026 12:00:00 GMT',
                 'Fri, 09 Oct 2026 11:59:59 GMT', 'Sat, 10 Oct 2026 12:00:01 GMT']
        pages = {BASE + 'search.rss': feed(*[(str(i + 100), title, date)
                 for i, (title, date) in enumerate(zip(TITLES, dates))]),
                 BASE + 'search/new': search(*[listing(str(i + 100), title)
                                               for i, title in enumerate(TITLES)])}
        pages.update({BASE + 'positions/' + str(i + 100): detail(title)
                      for i, title in enumerate(TITLES)})
        self.assert_recent_report('bii', lambda request, **k: io.BytesIO(pages[request.full_url].encode()))

    def test_kpmg_last24_command_includes_cutoff_and_excludes_old_and_future_jobs(self):
        from test_kpmg_jobs import vacancies, portal, detail
        from urllib.parse import urlsplit
        dates = ['Sat Oct 10 11:00:00 UTC 2026', 'Fri Oct 09 12:00:00 UTC 2026',
                 'Fri Oct 09 11:59:59 UTC 2026', 'Sat Oct 10 12:00:01 UTC 2026']
        def http(request, **kwargs):
            url = urlsplit(request.full_url)
            if url.netloc == 'www.kpmgcareers.co.uk':
                source = vacancies([(str(i + 100), title, 'Full Time', 'Advisory')
                                    for i, title in enumerate(TITLES)])
            elif url.path == '/search':
                source = portal([(str(i + 200), title) for i, title in enumerate(TITLES)])
            else:
                index = int(url.path.split('/')[3]) - 200
                source = detail(str(index + 100), dates[index])
            return io.BytesIO(source.encode())
        self.assert_recent_report('kpmg', http)

    def test_google_last24_command_uses_earliest_timestamp_for_each_opening(self):
        import json
        rows = []
        for i, (title, stamp) in enumerate(zip(TITLES, [1791630000, 1791547200,
                                                       1791547199, 1791633601])):
            raw = [None] * 21
            raw[0], raw[1], raw[7], raw[9] = str(i + 100), title, 'Google', [['London, UK']]
            raw[12], raw[13], raw[14] = [stamp], [stamp + 1], [stamp + 2]
            raw[19], raw[20] = [None, '<p>Bachelors degree</p>'], 1
            rows.append(raw)
        source = '<script>AF_initDataCallback({data:' + json.dumps([rows, None, 4, 20]) + '});</script>'
        self.assert_recent_report('google', lambda *a, **k: io.BytesIO(source.encode()))
