# Election Spend Dashboard

Streamlit dashboard for 2026 political spend, polling, FEC/PAC context,
House race rosters, and political advertising windows by market.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app/dashboard.py
```

## Bulk ingest the Political Spend folder

```bash
python ingest_all_sources.py --directory "/Users/gerritmaxwell/Downloads/Political Spend"
```

The bulk loader routes each supported file family automatically:

- Race spend workbooks: House, Senate, Governor, Downballot
- Aggregate spend workbooks: Spend by State, Spend by Market
- Polling workbook: RealClearPolling wide export
- Political-window workbooks by market/DMA
- House roster and House party/district lookup workbooks
- FEC/PAC files: `cn.txt`, `ccl.txt`, `weball26.txt`, `webl26.txt`,
  `webk26.txt`, `committee_summary_2026.csv`, `leadership2026.csv`,
  and `PAC_to_Candidates_2026*.csv`

After ingest, the script rebuilds the SQLite analysis tables used by the
dashboard.

## App entry points

- Streamlit app: `app/dashboard.py`
- Bulk loader: `ingest_all_sources.py`
- Spend loader: `ingest_advertiser_file.py`
- FEC/PAC loader: `ingest_fec_files.py`
- Polling loader: `ingest_polls.py`
- House roster loader: `ingest_house_roster.py`
