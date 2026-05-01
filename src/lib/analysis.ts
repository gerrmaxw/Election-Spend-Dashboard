import type { CampaignAnalysis, CampaignSummary, ElectionResult, MediaBucket, SpendRow } from '../types'

const EMPTY_MEDIA_TOTALS: Record<MediaBucket, number> = {
  broadcast: 0,
  cable: 0,
  ctv: 0,
  digital: 0,
  other: 0,
}

function normalizeCandidate(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function buildTvMixLabel(mediaSpend: Record<MediaBucket, number>) {
  const hasBroadcast = mediaSpend.broadcast > 0
  const hasCable = mediaSpend.cable > 0
  const hasCtv = mediaSpend.ctv > 0

  if (hasBroadcast && !hasCable && !hasCtv) {
    return 'Broadcast only'
  }
  if (!hasBroadcast && (hasCable || hasCtv)) {
    return 'Cable / CTV only'
  }
  if (hasBroadcast && (hasCable || hasCtv)) {
    return 'Broadcast + targeted TV'
  }
  return 'No TV'
}

function matchElectionResult(results: ElectionResult[], row: SpendRow) {
  const exact = results.find(
    (result) =>
      result.normalizedCandidate === row.normalizedCandidate &&
      (!row.state || result.state === row.state),
  )

  if (exact) {
    return { result: exact, confidence: 'Exact candidate + state' }
  }

  const fallback = results.find((result) => result.normalizedCandidate === row.normalizedCandidate)
  if (fallback) {
    return { result: fallback, confidence: 'Candidate-only match' }
  }

  return { result: null, confidence: 'Unmatched' }
}

export function buildCampaignAnalysis(
  results: ElectionResult[],
  rawSpendRows: SpendRow[],
): CampaignAnalysis {
  const resultLookup = results.map((result) => ({
    ...result,
    normalizedCandidate: normalizeCandidate(result.candidateName),
  }))

  const grouped = new Map<string, CampaignSummary>()
  let matchedCampaigns = 0

  // Only rows classified as candidate spending (candidate committee, joint
  // candidate/PAC, or recognized candidate-name pattern) feed the campaign
  // cohort. Super PACs, party committees, and issue/ballot-measure groups
  // are excluded so their dollars do not get attributed to a candidate.
  const candidateSpendRows = rawSpendRows.filter((row) => row.isCandidate)

  for (const row of candidateSpendRows) {
    const key = [row.normalizedCandidate, row.state || 'NA', row.office || 'NA'].join('|')
    const match = matchElectionResult(resultLookup, row)

    if (!grouped.has(key)) {
      const matchedResult = match.result
      if (matchedResult) {
        matchedCampaigns += 1
      }

      grouped.set(key, {
        id: key,
        candidateName: row.candidateName,
        state: row.state || matchedResult?.state || '',
        office: row.office || matchedResult?.office || '',
        district: row.district || matchedResult?.district || '',
        totalSpend: 0,
        mediaSpend: { ...EMPTY_MEDIA_TOTALS },
        outcome: matchedResult ? (matchedResult.winner ? 'Won' : 'Lost') : 'Unknown',
        tvMixLabel: 'No TV',
        matchConfidence: match.confidence,
      })
    }

    const current = grouped.get(key)!
    current.totalSpend += row.spend
    current.mediaSpend[row.mediaBucket] += row.spend
    current.tvMixLabel = buildTvMixLabel(current.mediaSpend)
  }

  const campaigns = Array.from(grouped.values()).sort((left, right) => right.totalSpend - left.totalSpend)
  const mediaTotals = campaigns.reduce<Record<MediaBucket, number>>(
    (totals, campaign) => {
      totals.broadcast += campaign.mediaSpend.broadcast
      totals.cable += campaign.mediaSpend.cable
      totals.ctv += campaign.mediaSpend.ctv
      totals.digital += campaign.mediaSpend.digital
      totals.other += campaign.mediaSpend.other
      return totals
    },
    { ...EMPTY_MEDIA_TOTALS },
  )

  const broadcastOnly = campaigns.filter((campaign) => campaign.tvMixLabel === 'Broadcast only')
  const cableOrCtv = campaigns.filter((campaign) =>
    ['Cable / CTV only', 'Broadcast + targeted TV'].includes(campaign.tvMixLabel),
  )

  const broadcastWins = broadcastOnly.filter((campaign) => campaign.outcome === 'Won').length
  const cableWins = cableOrCtv.filter((campaign) => campaign.outcome === 'Won').length

  return {
    rawSpendRows,
    campaigns,
    summary: {
      matchedCampaigns,
      mediaTotals,
      broadcastOnlySpend: broadcastOnly.reduce((sum, campaign) => sum + campaign.totalSpend, 0),
      broadcastOnly: {
        count: broadcastOnly.length,
        wins: broadcastWins,
        losses: broadcastOnly.filter((campaign) => campaign.outcome === 'Lost').length,
        winRate: broadcastOnly.length ? broadcastWins / broadcastOnly.length : 0,
      },
      cableOrCtv: {
        count: cableOrCtv.length,
        wins: cableWins,
        losses: cableOrCtv.filter((campaign) => campaign.outcome === 'Lost').length,
        winRate: cableOrCtv.length ? cableWins / cableOrCtv.length : 0,
      },
    },
  }
}

export function buildStrategicNarrative(analysis: CampaignAnalysis) {
  const { summary } = analysis

  if (!analysis.rawSpendRows.length) {
    return {
      headline:
        'Load a spend file to generate the candidate-level narrative. Sample election results are already available as a fallback baseline.',
      bullets: [
        'Broadcast-only cohorts are isolated from uploaded media labels.',
        'Cable and CTV are treated as targeted-TV channels for the pitch.',
        'The matching layer joins uploaded spend rows to election winners and losers.',
      ],
      caveat:
        'Without uploaded spend, the dashboard can show the election universe but not the media mix argument.',
    }
  }

  if (!summary.broadcastOnly.count) {
    return {
      headline:
        'No matched campaigns in the upload went broadcast-only. That can itself support a pitch that serious campaigns no longer rely on untargeted TV alone.',
      bullets: [
        `Cable or CTV appeared in ${summary.cableOrCtv.count} matched campaign records.`,
        `Broadcast accounted for ${Math.round(summary.mediaTotals.broadcast)} dollars, but none of the matched TV buyers stayed broadcast-exclusive.`,
        'Use this as a market-read finding instead of forcing a weak causal claim.',
      ],
      caveat:
        'If you want a direct broadcast-only win / loss comparison, the uploaded file needs campaigns whose TV mix excludes cable and CTV.',
    }
  }

  return {
    headline: `Broadcast-only campaigns in this matched cohort won ${Math.round(
      summary.broadcastOnly.winRate * 100,
    )}% of the time, versus ${Math.round(summary.cableOrCtv.winRate * 100)}% for campaigns that used cable or CTV.`,
    bullets: [
      `${summary.broadcastOnly.losses} broadcast-only campaigns lost after spending ${Math.round(summary.broadcastOnlySpend).toLocaleString()} dollars.`,
      `${summary.cableOrCtv.wins} campaigns with cable or CTV won in the matched set.`,
      'District and sub-state races are the cleanest proof points for the broadcast-waste story because DMA coverage spills outside the actual electorate.',
    ],
    caveat:
      'This readout is directional, not causal proof. Budget, incumbency, and race competitiveness still need controls before using it as a hard effectiveness claim.',
  }
}
