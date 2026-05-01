import { useMemo, useState } from 'react'
import * as XLSX from 'xlsx'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import './App.css'
import { buildCampaignAnalysis, buildStrategicNarrative } from './lib/analysis'
import { CLAUDE_LEGACY_MODULES } from './lib/claudeLegacy'
import { SAMPLE_ELECTION_RESULTS } from './data/sampleElectionResults'
import { SAMPLE_SPEND_ROWS } from './data/sampleSpendRows'
import { parseSpendRows } from './lib/spendParser'
import type { CampaignAnalysis, ElectionResult, FetchState, ViewMode } from './types'

const STATE_OPTIONS = [
  'CA',
  'TX',
  'GA',
  'IL',
  'VA',
  'MI',
  'NY',
  'LA',
  'NH',
  'SC',
  'FL',
  'PA',
  'NJ',
  'MD',
  'IN',
  'TN',
  'AR',
  'CO',
  'MN',
  'NM',
  'MS',
  'OR',
  'MA',
  'WA',
  'DC',
  'CT',
  'DE',
  'VT',
] as const

const DEFAULT_STATES = ['TX', 'IL', 'GA', 'PA', 'MI', 'VA']

const MEDIA_COLORS = {
  broadcast: '#d95f02',
  cable: '#1b9e77',
  ctv: '#7570b3',
  digital: '#4c78a8',
  other: '#9aa4b2',
} as const

function formatCurrency(value: number) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(Math.round(value))
}

function formatPercent(value: number) {
  return `${(value * 100).toFixed(1)}%`
}

function formatShare(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '—'
  if (value < 0.001) return '<0.1%'
  return `${(value * 100).toFixed(1)}%`
}

async function readElectionResults(states: string[], year: number) {
  const params = new URLSearchParams({
    states: states.join(','),
    year: String(year),
    limit: '250',
  })
  const response = await fetch(`/api/civic/search?${params.toString()}`)
  if (!response.ok) {
    throw new Error(`Election API request failed with ${response.status}`)
  }
  const payload = (await response.json()) as { results: ElectionResult[]; source: string }
  return payload
}

function App() {
  const [viewMode, setViewMode] = useState<ViewMode>('overview')
  const [year, setYear] = useState(2026)
  const [selectedStates, setSelectedStates] = useState<string[]>(DEFAULT_STATES)
  const [fetchState, setFetchState] = useState<FetchState>('idle')
  const [fetchError, setFetchError] = useState('')
  const [sourceLabel, setSourceLabel] = useState('Sample data')
  const [results, setResults] = useState<ElectionResult[]>(SAMPLE_ELECTION_RESULTS)
  const [uploadName, setUploadName] = useState('Built-in demo spend')
  const [analysis, setAnalysis] = useState<CampaignAnalysis>(
    buildCampaignAnalysis(SAMPLE_ELECTION_RESULTS, SAMPLE_SPEND_ROWS),
  )

  const summary = useMemo(() => analysis.summary, [analysis])

  const mediaMixData = useMemo(
    () =>
      [
        { label: 'Broadcast', value: summary.mediaTotals.broadcast, fill: MEDIA_COLORS.broadcast },
        { label: 'Cable', value: summary.mediaTotals.cable, fill: MEDIA_COLORS.cable },
        { label: 'CTV', value: summary.mediaTotals.ctv, fill: MEDIA_COLORS.ctv },
        { label: 'Digital', value: summary.mediaTotals.digital, fill: MEDIA_COLORS.digital },
      ].filter((entry) => entry.value > 0),
    [summary.mediaTotals],
  )

  const winRateData = [
    {
      cohort: 'Broadcast only',
      winRate: Number((summary.broadcastOnly.winRate * 100).toFixed(1)),
      count: summary.broadcastOnly.count,
      fill: '#d95f02',
    },
    {
      cohort: 'Cable / CTV',
      winRate: Number((summary.cableOrCtv.winRate * 100).toFixed(1)),
      count: summary.cableOrCtv.count,
      fill: '#1b9e77',
    },
  ]

  const agentNarrative = useMemo(() => buildStrategicNarrative(analysis), [analysis])

  const totalCampaignSpend = useMemo(
    () => analysis.campaigns.reduce((sum, campaign) => sum + campaign.totalSpend, 0),
    [analysis.campaigns],
  )

  const nonCandidateSpend = useMemo(() => {
    let total = 0
    let rows = 0
    const advertisers = new Set<string>()
    for (const row of analysis.rawSpendRows) {
      if (row.isCandidate) continue
      total += row.spend
      rows += 1
      if (row.advertiserName) advertisers.add(row.advertiserName)
    }
    return { total, rows, advertisers: advertisers.size }
  }, [analysis.rawSpendRows])

  async function handleFetchResults() {
    setFetchState('loading')
    setFetchError('')

    try {
      const payload = await readElectionResults(selectedStates, year)
      setResults(payload.results)
      setSourceLabel(payload.source)
      setAnalysis((current) => buildCampaignAnalysis(payload.results, current.rawSpendRows))
      setFetchState('success')
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Unable to pull election results right now.'
      setFetchError(message)
      setFetchState('error')
    }
  }

  async function handleSpendUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]

    if (!file) {
      return
    }

    const workbook = XLSX.read(await file.arrayBuffer(), { type: 'array' })
    const firstSheet = workbook.Sheets[workbook.SheetNames[0]]
    const rows = XLSX.utils.sheet_to_json<Record<string, unknown>>(firstSheet, {
      defval: '',
      raw: false,
    })

    const parsedRows = parseSpendRows(rows)
    setUploadName(file.name)
    setAnalysis(buildCampaignAnalysis(results, parsedRows))
  }

  function toggleState(state: string) {
    setSelectedStates((current) =>
      current.includes(state) ? current.filter((item) => item !== state) : [...current, state],
    )
  }

  function loadDemoSpend() {
    setUploadName('Built-in demo spend')
    setAnalysis(buildCampaignAnalysis(results, SAMPLE_SPEND_ROWS))
  }

  return (
    <div className="app-shell">
      <header className="hero-band">
        <div>
          <p className="eyebrow">Election Media Mix Dashboard</p>
          <h1>Broadcast waste vs. targeted TV performance</h1>
          <p className="hero-copy">
            Pull election results from CivicAPI, upload spend by media type from Excel or CSV,
            isolate broadcast-only campaigns, and compare their outcomes to campaigns that used
            cable or CTV.
          </p>
        </div>
        <div className="hero-meta">
          <div>
            <span className="meta-label">Results source</span>
            <strong>{sourceLabel}</strong>
          </div>
          <div>
            <span className="meta-label">Spend file</span>
            <strong>{uploadName}</strong>
          </div>
          <div>
            <span className="meta-label">Matched campaigns</span>
            <strong>{summary.matchedCampaigns}</strong>
          </div>
        </div>
      </header>

      <section className="control-band">
        <div className="control-panel">
          <div className="panel-heading">
            <h2>Election API</h2>
            <span className={`status-pill status-${fetchState}`}>
              {fetchState === 'idle' && 'Ready'}
              {fetchState === 'loading' && 'Loading'}
              {fetchState === 'success' && 'Loaded'}
              {fetchState === 'error' && 'Retry needed'}
            </span>
          </div>
          <div className="inline-controls">
            <label>
              Year
              <input
                type="number"
                min={2025}
                max={2030}
                value={year}
                onChange={(event) => setYear(Number(event.target.value))}
              />
            </label>
            <button type="button" className="action-button" onClick={handleFetchResults}>
              Refresh results
            </button>
          </div>
          <div className="state-grid" role="group" aria-label="State selection">
            {STATE_OPTIONS.map((state) => (
              <button
                type="button"
                key={state}
                className={selectedStates.includes(state) ? 'state-chip active' : 'state-chip'}
                onClick={() => toggleState(state)}
              >
                {state}
              </button>
            ))}
          </div>
          <p className="note">
            CivicAPI documents support beginning on January 4, 2025, and exposes race search plus
            full race detail endpoints. This local dashboard proxies those requests so the browser
            is not blocked by cross-origin rules.
          </p>
          {fetchError ? <p className="error-text">{fetchError}</p> : null}
        </div>

        <div className="control-panel">
          <div className="panel-heading">
            <h2>Spend Upload</h2>
            <span className="status-pill status-idle">Excel / CSV</span>
          </div>
          <label className="upload-zone">
            <input type="file" accept=".csv,.xlsx,.xls" onChange={handleSpendUpload} />
            <span>Choose media-spend file</span>
            <small>
              Expected columns can be messy. The parser looks for candidate, state, office, media
              type, and spend amount aliases automatically.
            </small>
          </label>
          <div className="field-hints">
            <span>Broadcast: local spot, OTA, broadcast network</span>
            <span>Cable: Comcast, cable net, interconnect</span>
            <span>CTV: CTV, OTT, connected TV</span>
          </div>
          <button type="button" className="secondary-button" onClick={loadDemoSpend}>
            Load demo spend
          </button>
          <p className="note">
            The analysis engine is deterministic: upload parsing and media normalization are
            flexible, but cohort math, win rates, and outcome comparisons are computed in code.
          </p>
        </div>
      </section>

      <section className="kpi-band">
        <article className="kpi-tile">
          <span className="kpi-label">Broadcast-only win rate</span>
          <strong>{formatPercent(summary.broadcastOnly.winRate)}</strong>
          <small>{summary.broadcastOnly.count} campaigns</small>
        </article>
        <article className="kpi-tile">
          <span className="kpi-label">Cable / CTV win rate</span>
          <strong>{formatPercent(summary.cableOrCtv.winRate)}</strong>
          <small>{summary.cableOrCtv.count} campaigns</small>
        </article>
        <article className="kpi-tile">
          <span className="kpi-label">Broadcast-only losses</span>
          <strong>{summary.broadcastOnly.losses}</strong>
          <small>{summary.broadcastOnly.wins} wins</small>
        </article>
        <article className="kpi-tile">
          <span className="kpi-label">Broadcast spend at risk</span>
          <strong>{formatCurrency(summary.broadcastOnlySpend)}</strong>
          <small>{formatCurrency(summary.mediaTotals.broadcast)} total broadcast</small>
        </article>
        <article className="kpi-tile kpi-tile--muted">
          <span className="kpi-label">Non-candidate spend (excluded)</span>
          <strong>{formatCurrency(nonCandidateSpend.total)}</strong>
          <small>
            {nonCandidateSpend.advertisers} advertisers · {nonCandidateSpend.rows} rows
          </small>
        </article>
      </section>

      <section className="mode-strip" aria-label="View mode">
        {(['overview', 'campaigns', 'legacy'] as ViewMode[]).map((mode) => (
          <button
            key={mode}
            type="button"
            className={viewMode === mode ? 'mode-button active' : 'mode-button'}
            onClick={() => setViewMode(mode)}
          >
            {mode === 'overview' && 'Overview'}
            {mode === 'campaigns' && 'Campaign drilldown'}
            {mode === 'legacy' && 'Claude legacy'}
          </button>
        ))}
      </section>

      {viewMode === 'overview' ? (
        <>
          <section className="analytics-band">
            <article className="chart-panel">
              <div className="panel-heading">
                <h2>Win-rate comparison</h2>
                <span className="subtle-tag">Matched outcomes only</span>
              </div>
              <div className="chart-shell">
                <ResponsiveContainer width="100%" height={300}>
                  <BarChart data={winRateData}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="cohort" tickLine={false} axisLine={false} />
                    <YAxis tickFormatter={(value) => `${value}%`} axisLine={false} tickLine={false} />
                    <Tooltip
                      formatter={(value) =>
                        typeof value === 'number' ? `${value}%` : String(value ?? '')
                      }
                    />
                    <Bar dataKey="winRate" radius={[6, 6, 0, 0]}>
                      {winRateData.map((entry) => (
                        <Cell key={entry.cohort} fill={entry.fill} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </article>

            <article className="chart-panel">
              <div className="panel-heading">
                <h2>Spend by media bucket</h2>
                <span className="subtle-tag">Uploaded file only</span>
              </div>
              <div className="chart-shell">
                <ResponsiveContainer width="100%" height={300}>
                  <PieChart>
                    <Pie
                      data={mediaMixData}
                      dataKey="value"
                      nameKey="label"
                      cx="50%"
                      cy="50%"
                      innerRadius={68}
                      outerRadius={112}
                      paddingAngle={2}
                    >
                      {mediaMixData.map((entry) => (
                        <Cell key={entry.label} fill={entry.fill} />
                      ))}
                    </Pie>
                    <Tooltip
                      formatter={(value) =>
                        typeof value === 'number' ? formatCurrency(value) : String(value ?? '')
                      }
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="legend-list">
                {mediaMixData.map((entry) => (
                  <div key={entry.label} className="legend-item">
                    <span className="swatch" style={{ backgroundColor: entry.fill }} />
                    <span>{entry.label}</span>
                    <strong>{formatCurrency(entry.value)}</strong>
                  </div>
                ))}
              </div>
            </article>
          </section>

          <section className="insight-band">
            <article className="agent-panel">
              <div className="panel-heading">
                <h2>Campaign agent readout</h2>
                <span className="subtle-tag">Reusable pitch narrative</span>
              </div>
              <p>{agentNarrative.headline}</p>
              <ul className="insight-list">
                {agentNarrative.bullets.map((bullet) => (
                  <li key={bullet}>{bullet}</li>
                ))}
              </ul>
              <p className="note">{agentNarrative.caveat}</p>
            </article>

            <article className="agent-panel">
              <div className="panel-heading">
                <h2>Method flags</h2>
                <span className="subtle-tag">What to stress-test</span>
              </div>
              <ul className="insight-list">
                <li>Control for total spend before claiming causal lift from cable or CTV.</li>
                <li>
                  Broadcast-waste framing is strongest for district or county races that sit inside a
                  larger DMA.
                </li>
                <li>
                  Win / loss is useful for sales storytelling, but vote share and margin remain
                  stronger analytical endpoints.
                </li>
                <li>
                  Any campaign that mixes cable or CTV is treated as targeted-TV capable, even
                  if it also bought broadcast.
                </li>
              </ul>
            </article>
          </section>
        </>
      ) : null}

      {viewMode === 'campaigns' ? (
        <section className="table-band">
          <div className="table-header">
            <h2>Campaign drilldown</h2>
            <span>
              {analysis.campaigns.length} campaigns · {formatCurrency(totalCampaignSpend)} total spend
            </span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>State</th>
                  <th>Office</th>
                  <th>Outcome</th>
                  <th>TV mix</th>
                  <th className="num">Broadcast</th>
                  <th className="num">Cable</th>
                  <th className="num">CTV</th>
                  <th className="num">Total spend</th>
                  <th className="num">% of total</th>
                  <th>Match</th>
                </tr>
              </thead>
              <tbody>
                {analysis.campaigns.map((campaign) => {
                  const share =
                    totalCampaignSpend > 0 ? campaign.totalSpend / totalCampaignSpend : 0
                  return (
                    <tr key={campaign.id}>
                      <td>{campaign.candidateName}</td>
                      <td>{campaign.state || '—'}</td>
                      <td>{campaign.office || '—'}</td>
                      <td>
                        <span className={campaign.outcome === 'Won' ? 'pill win' : 'pill loss'}>
                          {campaign.outcome}
                        </span>
                      </td>
                      <td>{campaign.tvMixLabel}</td>
                      <td className="num">{formatCurrency(campaign.mediaSpend.broadcast)}</td>
                      <td className="num">{formatCurrency(campaign.mediaSpend.cable)}</td>
                      <td className="num">{formatCurrency(campaign.mediaSpend.ctv)}</td>
                      <td className="num">{formatCurrency(campaign.totalSpend)}</td>
                      <td className="num">
                        <div className="share-cell">
                          <span className="share-bar" aria-hidden="true">
                            <span
                              className="share-bar-fill"
                              style={{ width: `${Math.min(100, share * 100).toFixed(1)}%` }}
                            />
                          </span>
                          <span className="share-value">{formatShare(share)}</span>
                        </div>
                      </td>
                      <td>{campaign.matchConfidence}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {viewMode === 'legacy' ? (
        <section className="legacy-band">
          <div className="table-header">
            <h2>Recovered Claude build map</h2>
            <span>Session-derived architecture carried into this dashboard</span>
          </div>
          <div className="legacy-grid">
            {CLAUDE_LEGACY_MODULES.map((module) => (
              <article key={module.file} className="legacy-tile">
                <span className="legacy-file">{module.file}</span>
                <h3>{module.title}</h3>
                <p>{module.summary}</p>
              </article>
            ))}
          </div>
          <p className="note legacy-note">
            I was able to recover the prior file map, architecture, and analytical behavior from the
            Claude session. The exact source bundle was not directly downloadable into this new
            workspace, so the app here reimplements those modules in a single working dashboard.
          </p>
        </section>
      ) : null}
    </div>
  )
}

export default App
