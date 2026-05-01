export type MediaBucket = 'broadcast' | 'cable' | 'ctv' | 'digital' | 'other'
export type Outcome = 'Won' | 'Lost' | 'Unknown'
export type FetchState = 'idle' | 'loading' | 'success' | 'error'
export type ViewMode = 'overview' | 'campaigns' | 'legacy'

export interface ElectionResult {
  raceId: string
  candidateName: string
  normalizedCandidate: string
  electionName: string
  office: string
  electionScope: string
  state: string
  district: string
  electionDate: string
  party: string
  votes: number
  voteShare: number
  winner: boolean
}

export interface SpendRow {
  id: string
  advertiserName: string
  candidateName: string
  normalizedCandidate: string
  state: string
  office: string
  district: string
  mediaTypeRaw: string
  mediaBucket: MediaBucket
  spend: number
  affiliationType: string
  partyAffiliation: string
  isCandidate: boolean
  candidateClassification: 'candidate-committee' | 'candidate-pac' | 'name-pattern' | 'non-candidate'
  sourceRow: Record<string, unknown>
}

export interface CampaignSummary {
  id: string
  candidateName: string
  state: string
  office: string
  district: string
  totalSpend: number
  mediaSpend: Record<MediaBucket, number>
  outcome: Outcome
  tvMixLabel: string
  matchConfidence: string
}

export interface CampaignAnalysis {
  rawSpendRows: SpendRow[]
  campaigns: CampaignSummary[]
  summary: {
    matchedCampaigns: number
    mediaTotals: Record<MediaBucket, number>
    broadcastOnlySpend: number
    broadcastOnly: {
      count: number
      wins: number
      losses: number
      winRate: number
    }
    cableOrCtv: {
      count: number
      wins: number
      losses: number
      winRate: number
    }
  }
}
