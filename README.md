# Election Media Mix Dashboard

Local React dashboard for comparing election outcomes against uploaded campaign spend by media type.

## What it does

- Pulls election results through a local CivicAPI proxy
- Accepts manual Excel or CSV uploads for campaign media spend
- Normalizes messy media labels into `broadcast`, `cable`, `ctv`, `digital`, or `other`
- Joins uploaded spend to election winners and losers
- Highlights broadcast-only campaigns against campaigns that used cable or CTV
- Preserves the main architecture themes recovered from the earlier Claude session

## Run locally

```bash
npm install
npm run dev
```

Then open the local Vite URL shown in the terminal.

## Notes

- The dashboard ships with sample election results so the interface loads immediately.
- CivicAPI documents race search and race detail endpoints, with supported data beginning in 2025.
- The browser calls CivicAPI through a Vite dev proxy so local testing is not blocked by cross-origin behavior.
- The exact Claude source bundle was not directly downloadable into this fresh workspace, so the current app reimplements the recovered modules and analysis flow here.
- The Claude Python files you surfaced are preserved in [claude_python](/Users/gerritmaxwell/Documents/Codex/2026-04-23-build-a-custom-dashboard-and-agent/claude_python).
