# Weekly Polling Research — Broadcast Waste Analyzer

You are running a weekly autonomous research job. Goal: find any new public polling for the active 2026 primary and general election races tracked by the Broadcast Waste Analyzer, then write the results into the local SQLite database so the Polling Watchlist page updates.

## Working directory
`/Users/gerritmaxwell/Documents/Codex/2026-04-23-build-a-custom-dashboard-and-agent`

## Steps

1. Pull the list of active races to research. **Prioritize US House races** — they're the core pitch target because House districts are subsets of DMAs, which is where broadcast waste is sharpest.
   ```bash
   # House races first (primary audience for this research)
   sqlite3 local_data/broadcast_waste_analyzer.sqlite \
     "SELECT race_id, state, office, district, race_name, primary_date
      FROM upcoming_primaries
      WHERE primary_date >= date('now') AND primary_date <= date('now','+180 days')
        AND office = 'us_house'
      ORDER BY primary_date, state, district;"

   # Then non-House races (Senate/Governor/AG/statewide) as secondary coverage
   sqlite3 local_data/broadcast_waste_analyzer.sqlite \
     "SELECT race_id, state, office, district, race_name, primary_date
      FROM upcoming_primaries
      WHERE primary_date >= date('now') AND primary_date <= date('now','+180 days')
        AND office != 'us_house'
      ORDER BY primary_date, state, office;"
   ```
   Also include completed primaries from the current cycle with a general election still ahead:
   ```bash
   sqlite3 local_data/broadcast_waste_analyzer.sqlite \
     "SELECT DISTINCT r.race_id, r.state, r.office, r.district, r.election_name, r.general_date
      FROM races r
      WHERE r.cycle = 2026 AND (r.general_date IS NULL OR r.general_date >= date('now'));"
   ```

2. For each race, run a targeted web search. Suggested queries:
   - `"[state] [office] [district] primary poll 2026"`
   - `"[state] governor poll 2026"` / `"[state] senate poll 2026"`
   - For House: `"[state]-[district] poll 2026"` (e.g., `"CA-27 poll 2026"`)
   - Include site-restricted searches when useful: `site:realclearpolling.com`, `site:fivethirtyeight.com`, `site:nytimes.com`, `site:politico.com`, `site:270towin.com`, university pollsters (Roanoke College, Marist, Siena, Quinnipiac, Suffolk, Monmouth, Emerson).

3. Extract these fields from each poll you find:
   - `state` (2-letter), `office` (one of `us_house`, `us_senate`, `governor`, `attorney_general`, `state_senate`, `state_house`, `other`)
   - `district` (string or null)
   - `pollster` (organization name)
   - `start_date` / `end_date` (ISO `YYYY-MM-DD`)
   - `sample_size` (integer) and `population` (`LV`, `RV`, or `A`)
   - `sponsors` (if any)
   - `url` (the source page)
   - `candidates`: array of `{name, party, pct}`

4. Deduplicate against what's already in the DB:
   ```bash
   sqlite3 local_data/broadcast_waste_analyzer.sqlite \
     "SELECT state, office, district, pollster, start_date, end_date FROM polls;"
   ```
   Skip polls you've already ingested (match on state+office+district+pollster+dates).

5. Write the new polls to a JSON file, then ingest:
   ```bash
   # Write the array of poll objects to /tmp/polls_weekly.json, then:
   cd /Users/gerritmaxwell/Documents/Codex/2026-04-23-build-a-custom-dashboard-and-agent
   .venv39/bin/python ingest_polls.py /tmp/polls_weekly.json
   ```

6. Append a one-line summary to the run log:
   ```bash
   echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) new=<inserted> updated=<updated> skipped=<skipped>" \
     >> local_data/case_studies/polling_research_log.txt
   ```

## Rules
- **House races are the priority.** Spend the bulk of your search budget on US House primaries and generals; Senate/Governor are useful context but secondary.
- Only include polls from the current 2026 cycle.
- Prefer named pollsters with public methodology. Skip internal campaign polls unless that's all that exists (flag them in `sponsors`).
- If a race has no new polling, don't write anything for it — skipping is fine.
- If you find conflicting percentages (e.g., 2024 poll misdated as 2026), discard.
- Be conservative: a missed poll is better than a hallucinated one.

## Output
When done, respond with:
- Total new polls inserted / updated / skipped
- Races now covered vs. races still with no polling
- Any notable polling-vs-TV mismatches you noticed in the results
