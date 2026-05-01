export const CLAUDE_LEGACY_MODULES = [
  {
    file: 'campaign_analyzer/data/schema.py',
    title: 'Canonical campaign schema',
    summary:
      'Claude defined normalized models for races, candidates, spend rows, geo overlap, and campaign rollups so uploads and API data could land in one shape.',
  },
  {
    file: 'campaign_analyzer/data/store.py',
    title: 'SQLite persistence layer',
    summary:
      'The earlier build persisted fetched races and spend rollups with idempotent upserts, making the tool reusable across repeated imports.',
  },
  {
    file: 'campaign_analyzer/data/elections_client.py',
    title: 'Election results client',
    summary:
      'Claude wired election results to API fetches with caching and sample fallbacks so the app could still demo offline.',
  },
  {
    file: 'campaign_analyzer/agents/spend_normalizer.py',
    title: 'Spend normalization agent',
    summary:
      'The agent role was narrow: map messy spreadsheet columns and media labels into a canonical schema without letting the model touch the actual math.',
  },
  {
    file: 'campaign_analyzer/agents/entity_resolver.py',
    title: 'Entity matching layer',
    summary:
      'Rapid candidate-name matching connected uploaded advertiser names and committees back to election result entities.',
  },
  {
    file: 'campaign_analyzer/data/analysis.py',
    title: 'Deterministic cohort analysis',
    summary:
      'Win-rate comparisons, matched-cohort logic, and broadcast-waste framing lived in code rather than in generated prose.',
  },
  {
    file: 'fetch_civicapi_2026.py / fetch_fec_ie.py',
    title: 'Fetch utilities',
    summary:
      'Standalone scripts pulled race data and FEC independent expenditure support, then folded those records back into the core store.',
  },
  {
    file: 'app/dashboard.py',
    title: 'Dashboard shell',
    summary:
      'The Streamlit app used setup, cohort, waste, and race-explorer views. This React rebuild keeps those same analytical ideas in a denser front-end layout.',
  },
]
