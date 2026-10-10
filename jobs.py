#!/usr/bin/env python3
"""Fetch job reports from the company careers scripts."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import io
from pathlib import Path
import sys
import time


COMPANIES = ('anthropic', 'apple', 'bain', 'bcg', 'bii', 'deloitte', 'google',
             'kpmg', 'linkedin', 'mckinsey', 'meta', 'nvidia', 'openai', 'pa')


class OutputBuffer(io.StringIO):
    """Capture one company's output while preserving the destination's colour support."""
    def __init__(self, stream):
        super().__init__()
        self.stream = stream

    def isatty(self):
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()


def load_company(name):
    """Load a company script beside this entry point, regardless of working directory."""
    path = Path(__file__).resolve().with_name(name + '-jobs.py')
    spec = importlib.util.spec_from_file_location(name + '_jobs', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_company(name, last24, now):
    """Fetch one company independently and return its complete output."""
    with OutputBuffer(sys.stdout) as stdout, OutputBuffer(sys.stderr) as stderr:
        status = load_company(name).run(last24=last24, now=now,
                                        stdout=stdout, stderr=stderr, skip_empty=True)
        return status, stdout.getvalue(), stderr.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--companies', nargs='+', choices=COMPANIES, default=COMPANIES,
                        metavar='COMPANY', help='Companies to fetch (default: all). Choices: %(choices)s')
    window = parser.add_mutually_exclusive_group()
    window.add_argument('--last-24h', action='store_true', default=True,
                        help='Show only jobs posted in the last 24 hours (default).')
    window.add_argument('--last-30d', action='store_false', dest='last_24h',
                        help='Show the existing date groups covering the last 30 days.')
    args = parser.parse_args(argv)
    now = time.time()
    status = 0
    companies = tuple(dict.fromkeys(args.companies))
    printed_report = False
    with ThreadPoolExecutor(max_workers=len(companies)) as pool:
        pending = {pool.submit(run_company, name, args.last_24h, now): name for name in companies}
        for finished in as_completed(pending):
            try:
                result, report, error = finished.result()
            except Exception as failure:
                result, report = 1, ''
                error = f'Error: Could not fetch {pending[finished]} jobs.\n{failure}\n'
            if report:
                if printed_report:
                    sys.stdout.write('\n')
                sys.stdout.write(report)
                sys.stdout.flush()
                printed_report = True
            if error:
                sys.stderr.write(error)
                sys.stderr.flush()
            if result != 0:
                status = 1
    return status


if __name__ == '__main__':
    sys.exit(main())
