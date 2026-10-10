# Anthropic London jobs

Run `python3 anthropic-jobs.py`. Requires Python 3.8+ and internet access; no packages or account.

Matches London, UK in the location or any listed office, including multi-office postings. Prints the same non-overlapping 1 / 3 / 7 / 14 / 30 day groups as the existing scripts. Dates use `first_published` for each Greenhouse job post, not `updated_at`. Separate posts for the same role can appear separately.

## Filters you can choose

Checked against the public feed on 30 September 2026. Values and availability can change.

- **Location:** London, UK is already selected. Multi-office postings are included; say if you only want London-only postings.
- **Team / department:** choose any teams below, or keep all.
- **Location type:** On-Site, Hybrid (Travel-Required), Remote. Some listings have no value; these are included by default and excluded when a location-type filter is set. All three named types currently occur among London listings.
- **Title keywords to include:** any matching term, such as engineer, research, applied AI, sales, or finance.
- **Title keywords to exclude:** any matching term, such as staff, manager, director, fellows, or expression of interest. These are text matches, not reliable seniority or employment classifications.
- **Date window:** the report currently covers the last 30 days with five groups. Say if you want older jobs or different windows.

The public careers page has team and location selectors plus role search. The feed additionally exposes Location Type. Its list endpoint returns all published posts; the script applies these filters locally.

There are no structured seniority, degree, employment-type, years-of-experience, visa, or salary fields in the fetched list feed. Preferences for these would require separate job-description checks, with missing or ambiguous values handled explicitly. This script therefore includes fellowships, rotational programs, and expressions of interest by default and does not claim to filter full-time roles.

## Teams currently offering London postings

- AI Research & Engineering
- Applied AI
- Compute
- Engineering & Design - Product
- Finance
- Legal
- Public Benefit
- Safeguards (Trust & Safety)
- Sales
- Security
- Software Engineering - Infrastructure
- Technical Program Management

## All teams in the board

- AI Research & Engineering
- Applied AI
- Communications
- Compute
- Data Science & Analytics
- Engineering & Design - Product
- Finance
- Hardware
- Legal
- Marketing & Brand
- People
- Product Management, Support, & Operations
- Public Benefit
- Public Policy
- Safeguards (Trust & Safety)
- Sales
- Security
- Software Engineering - Infrastructure
- Technical Education
- Technical Program Management

## Setting the supported filters

Edit the tuples near the top of `anthropic-jobs.py`. Empty tuples mean no restriction. Team and location-type names use case-insensitive exact matches. Title terms use case-insensitive substring matches. Multiple values within a filter use OR; different filters combine with AND. Exclusions take priority.

```python
DEPARTMENTS = ('Applied AI', 'AI Research & Engineering')
LOCATION_TYPES = ('On-Site',)
TITLE_INCLUDE = ('engineer', 'architect')
TITLE_EXCLUDE = ('staff', 'manager', 'fellows', 'expression of interest')
```

Location-only and date-window preferences require changing the script; they are not configurable tuples.

Sources: [Anthropic careers jobs](https://www.anthropic.com/careers/jobs), [public Anthropic job feed](https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=true), [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html).
