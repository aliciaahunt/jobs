# Job scripts

Command-line Python scripts for finding recent London jobs across 14 employers.
Run all sources together with `jobs.py`, select a few, or run each company script
on its own. Reports contain job titles, ages, counts, and application links.

## Setup and quick start

You need **Python 3.9 or newer** to run the entire collection, and internet access
when fetching jobs. The scripts use only the Python standard library: no pip
packages, browser automation, API keys, accounts, or configuration are required.
Most individual scripts also support Python 3.8; Deloitte requires Python 3.9
because it uses `zoneinfo`.

Clone the repository:

```sh
git clone https://github.com/aliciaahunt/jobs.git
cd jobs
python3 --version
python3 jobs.py
```

If you already have the folder, run the last two commands from it. On Windows,
use `py -3` in place of `python3` if that is how your Python installation is
available. A virtual environment is optional because there are no project
package dependencies.

## Run several companies

The combined runner defaults to **all 14 sources, last 24 hours**:

```sh
# All sources, jobs dated within the last 24 hours.
python3 jobs.py

# Select any number of sources, separated by spaces.
python3 jobs.py --companies apple google openai
python3 jobs.py --companies apple

# Explicitly request the default window.
python3 jobs.py --companies anthropic pa --last-24h

# Expand to the last 30 days.
python3 jobs.py --last-30d
python3 jobs.py --companies bain bcg mckinsey --last-30d

# Show all supported flags and company names without fetching anything.
python3 jobs.py --help
```

| Option | Behavior |
| --- | --- |
| `--companies COMPANY [COMPANY ...]` | Fetch only the named sources; default is all. Repeated names run once. |
| `--last-24h` | Show the inclusive last 86,400 seconds; this is the default. |
| `--last-30d` | Show non-overlapping date groups covering the last 30 days, plus Bain's undated roles. |
| `-h`, `--help` | Print usage and exit. |

The two date flags are mutually exclusive. Company names must match the
lowercase names in the table below. Invalid arguments are rejected before
fetching jobs.

Selected sources run concurrently. Each complete report appears as soon as its
source finishes, so source order can vary. Reports stay together and output is
flushed immediately, including when redirected. All sources in a run share the
same report timestamp and cutoff.

Companies and date groups with no matching jobs are omitted. If nothing matches,
standard output is empty and the command succeeds. A failed source prints an
error immediately to standard error while the other sources continue. The
combined command exits with status `1` if any source fails.

## Every company script and its filters

Filters below describe the code's defaults. A London search can include multiple
offices or surrounding locations depending on the source. Employment, seniority,
and degree filters differ between sources.

| Company name | Script | Default scope | Date used |
| --- | --- | --- | --- |
| `anthropic` | [anthropic-jobs.py](anthropic-jobs.py) | London, UK in the location or office list, including multiple offices; all employment types, levels, and degrees. Optional team, location-type, and title filters. | Greenhouse `first_published` for each job post. |
| `apple` | [apple-jobs.py](apple-jobs.py) | Apple's London search, including London-area and UK-wide retail results; at least 35 listed weekly hours. Retail roles may also offer part-time work. | Apple `postDateInGMT`. |
| `bain` | [bain-jobs.py](bain-jobs.py) | London among listed offices; permanent, temporary, contract-to-permanent, and internship full-time roles; all roles, levels, and degrees. | Job-page `datePosted`, interpreted at midnight UTC; undated roles are listed separately in 30-day mode. |
| `bcg` | [bcg-jobs.py](bcg-jobs.py) | Full-time London, United Kingdom roles, including secondary offices. | BCG `postedDate`; republication is possible. |
| `bii` | [bii-jobs.py](bii-jobs.py) | British International Investment employee roles with London among their offices. Permanent and fixed-term roles are assumed full-time unless explicitly part-time or job-sharing; other types need explicit full-time wording. | Public recruitment RSS `pubDate`. |
| `deloitte` | [deloitte-jobs.py](deloitte-jobs.py) | London, including multiple offices; full-time `WPFULL` search across business lines and grades. | Displayed posting date, interpreted at midnight in `Europe/London`. |
| `google` | [google-jobs.py](google-jobs.py) | London search with Early or Mid level, Bachelor's or Master's degree, and full-time filters. | Earliest of three embedded timestamps; this is not a verified opening date. |
| `kpmg` | [kpmg-jobs.py](kpmg-jobs.py) | Experienced, full-time Advisory roles in London. | Experienced-professional portal date, joined by requisition ID; the main careers site's generated date is ignored. |
| `linkedin` | [linkedin-jobs.py](linkedin-jobs.py) | LinkedIn's own full-time London-area vacancies, across titles and experience levels. This does not search all employers on LinkedIn. | Relative minute/hour age when available; otherwise the guest card's calendar date at midnight UTC. |
| `mckinsey` | [mckinsey-jobs.py](mckinsey-jobs.py) | Active full-time roles with London, United Kingdom among hiring locations. | `jobOrderCreatedDate` at midnight UTC, which can predate republication. |
| `meta` | [meta-jobs.py](meta-jobs.py) | Full-time, London-only roles in the seven teams listed below; multiple-office roles are excluded. | Structured job-page `datePosted`. |
| `nvidia` | [nvidia-jobs.py](nvidia-jobs.py) | NVIDIA's full-time London search, including nearby and UK remote results. | NVIDIA `postedTs`. |
| `openai` | [openai-jobs.py](openai-jobs.py) | Listed full-time jobs with London, UK as a primary or secondary location; all levels and degrees. | Ashby `publishedAt`, a last-publication timestamp that may include republication. |
| `pa` | [pa-jobs.py](pa-jobs.py) | Public full-time PA Consulting postings in London, GB, across levels. | SmartRecruiters `releasedDate`, which may include republication. |

Meta's selected teams are Sales & Marketing; Product Management; Global
Operations; Infrastructure; Communications & Public Policy; Business Development
& Partnerships; and Legal, Finance, Facilities & Admin.

LinkedIn's unauthenticated guest search is not exhaustive and has a 1,000-result
pagination ceiling. All scripts depend on their external source's response
format and available listings; the offline tests do not verify live availability.

## Run one company

Each `*-jobs.py` script is standalone and can be copied and run separately.
**Individual scripts default to the 30-day report**, unlike the combined runner:

```sh
python3 apple-jobs.py
python3 google-jobs.py --last-24h
python3 openai-jobs.py --last-24h
python3 bain-jobs.py --help
```

Replace the filename with any script in the table. Each individual script accepts
`--last-24h` and `--help`; `--companies` and `--last-30d` belong to `jobs.py` only.
For an individual 30-day report, omit the date flag.

Individual scripts retain empty date sections and show an empty report if
nothing matches. The combined runner suppresses those empty sections.

## Read the reports

The 30-day report groups jobs into **last 24 hours**, **1–3 days ago**, **3–7 days
ago**, **7–14 days ago**, and **14–30 days ago**. A dated job appears only in its
youngest matching group, with newest jobs first within each group. Jobs older
than 30 days and future-dated jobs are excluded from the date groups. Bain also
has a **DATE UNAVAILABLE** group in 30-day mode.

The 24-hour report has a single **LAST 24 HOURS** section. It includes the exact
cutoff and excludes future dates and undated jobs. Date-only sources retain their
midnight interpretation, so the apparent age has only calendar-day precision.
The report's updated time uses your local timezone; each source's date meaning
is described in the table above.

The role count for the selected period can be smaller than the `matched` count,
which describes collected records. Counts and dates describe the source's
listings, not necessarily newly created openings. There is no persistent history
or notification state: running again can show the same jobs.

Colors are enabled for supported terminals and disabled for redirected output,
`TERM=dumb`, or when `NO_COLOR` is set. Prose wraps to terminal width while links
remain complete.

```sh
# Disable terminal colors.
NO_COLOR=1 python3 jobs.py --companies google openai

# Save output and errors separately; these files are ignored by Git.
python3 jobs.py --last-30d > jobs-report.txt 2> jobs-errors.log
```

The scripts fetch and print listings; open a report's application link to apply.

## Change the search filters

There is no shared configuration file or general filter CLI. Most search filters
are constants or checks near the top of each company script, such as Google's
`SEARCH_URL`, Apple's `MIN_WEEKLY_HOURS`, Meta's `TEAMS`, and Bain's `FILTERS` and
`FULL_TIME_TYPES`. Review the source's validation checks as well when changing
location or employment filters.

Anthropic has four supported filter tuples near the top of its script. Empty
tuples allow all values:

```python
DEPARTMENTS = ('Applied AI', 'AI Research & Engineering')
LOCATION_TYPES = ('On-Site',)
TITLE_INCLUDE = ('engineer', 'architect')
TITLE_EXCLUDE = ('staff', 'manager', 'fellows', 'expression of interest')
```

Department and location-type values use case-insensitive exact matching; title
terms use case-insensitive substring matching. Values within one filter combine
with OR, different filters combine with AND, and exclusions take priority.
[anthropic-jobs-filters.md](anthropic-jobs-filters.md) explains these filters and
contains a dated snapshot of team names. Use the CLI examples above for current
date-window behavior.

## Tests and maintenance

Run the complete offline suite:

```sh
python3 -m unittest discover -v

# Run just the combined-runner and 24-hour command tests.
python3 -m unittest test_jobs test_last24_jobs -v

# Run one company's tests.
python3 -m unittest test_apple_jobs -v
```

Tests simulate HTTP responses and fix the clock, so they require no network
access or third-party test runner. They check parsing, filtering, pagination,
incomplete or changing feeds, date boundaries, formatting, errors, and the
combined runner's concurrency and output flushing. The initial repository setup
passed all 148 tests on Python 3.9.6.

| File pattern | Purpose |
| --- | --- |
| `jobs.py` | Load selected company scripts relative to this file, run them concurrently, and print complete reports. It also works when invoked by absolute path from another directory. |
| `*-jobs.py` | Fetch, validate, filter, and format one source's jobs. |
| `test_<company>_jobs.py` | Offline tests for individual sources; Google-specific checks also appear in the shared command tests. |
| `test_jobs.py` | Combined runner's flags, source selection, errors, concurrency, and reporting. |
| `test_last24_jobs.py` | Cross-source 24-hour behavior and request optimizations. |
| `anthropic-jobs-filters.md` | Anthropic filter reference and historical team snapshot. |
| `.gitignore` | Exclude Python caches, virtual environments, local environment files, logs, saved reports, and editor/OS clutter. |

When adding a source, implement the same `run(last24=False, now=None,
stdout=None, stderr=None, skip_empty=False)` interface, place the script beside
`jobs.py`, add its name to `COMPANIES`, and update the tests and this README.
Company scripts intentionally include their own fetch and formatting code so
that they remain usable independently.

## Troubleshooting

- **No output from `jobs.py`:** no selected source had matching jobs. Try
  `--last-30d` or an individual script to see its empty sections. Check standard
  error and the exit status for failures.
- **Network errors or HTTP rate limits:** retry later. LinkedIn includes retries
  for temporary failures and rate limits; avoid repeatedly fetching in a tight
  loop. Requests generally have a 30-second timeout, but paging, detail requests,
  and retries can make an entire run take longer.
- **“Results changed” or incomplete-feed errors:** rerun. Many sources validate
  totals and duplicate IDs to avoid presenting incomplete results. Apple can
  automatically restart a changing search.
- **Unrecognized job data:** the source may have changed its HTML or API format;
  inspect the failing company's parser and update its offline fixtures/tests.
- **Missing `zoneinfo`:** use Python 3.9 or newer. If Deloitte reports missing
  `Europe/London` timezone data, provide a system timezone database or install
  the optional `tzdata` package with `python3 -m pip install tzdata` (often needed
  on Windows).
- **Unexpected recent jobs or missing openings:** check the date column above
  and source filters. Some timestamps reflect republication or job-order
  creation, and Google's timestamp fields have no public schema.

## Git workflow

The repository is public at [aliciaahunt/jobs](https://github.com/aliciaahunt/jobs).
After editing, run the tests and commit the changes:

```sh
python3 -m unittest discover -v
git status
git add README.md apple-jobs.py
git commit -m "Update Apple job search documentation"
git push
```

Stage the actual files you changed. The initial branch follows the local Git
default (`main`), and `origin` points to the GitHub repository. No
scheduled fetches or background services are installed.
