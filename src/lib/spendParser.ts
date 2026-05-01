import type { MediaBucket, SpendRow } from '../types'

function normalizeText(value: unknown) {
  return String(value ?? '')
    .trim()
    .toLowerCase()
}

function normalizeCandidate(value: unknown) {
  return normalizeText(value)
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function lookupKey(row: Record<string, unknown>, aliases: string[]) {
  const entries = Object.keys(row)
  const normalizedMap = new Map(entries.map((key) => [normalizeText(key), key]))

  for (const alias of aliases) {
    const direct = normalizedMap.get(alias)
    if (direct) {
      return direct
    }
  }

  return entries.find((key) => aliases.some((alias) => normalizeText(key).includes(alias)))
}

function classifyMediaBucket(rawValue: string): MediaBucket {
  const value = normalizeText(rawValue)

  if (
    value.includes('broadcast') ||
    value.includes('local spot') ||
    value.includes('ota') ||
    value.includes('network tv')
  ) {
    return 'broadcast'
  }

  if (
    value.includes('cable') ||
    value.includes('comcast') ||
    value.includes('interconnect') ||
    value.includes('ampersand')
  ) {
    return 'cable'
  }

  if (value.includes('stream') || value.includes('ott') || value.includes('ctv')) {
    return 'ctv'
  }

  if (value.includes('digital') || value.includes('online') || value.includes('display')) {
    return 'digital'
  }

  return 'other'
}

function parseSpend(value: unknown) {
  if (value === null || value === undefined || value === '') return 0
  const numeric = Number(String(value).replace(/[^0-9.-]/g, ''))
  return Number.isFinite(numeric) ? numeric : 0
}

// Affiliation-type strings that indicate a candidate's own committee or
// joint candidate/PAC structure. Anything matching these is treated as
// candidate spending regardless of the advertiser name.
const CANDIDATE_AFFILIATION_PATTERNS: RegExp[] = [
  /\bcandidate committee\b/,
  /\bcandidate\s*\/\s*pac\b/, // "Candidate/PAC"
  /\bcandidate pac\b/,
  /\bauthorized committee\b/,
  /\bprincipal campaign committee\b/,
  /\bcampaign committee\b/, // plain "campaign committee" almost always = candidate
]

// Affiliation-type strings that explicitly disqualify a row as candidate
// spending even if the advertiser name happens to look candidate-shaped.
const NON_CANDIDATE_AFFILIATION_PATTERNS: RegExp[] = [
  /\bsuper pac\b/,
  /\bparty committee\b/,
  /\bissue committee\b/,
  /\bballot[- ]measure\b/,
  /\bissue\/trade\b/,
  /\borganization\/issue advertiser\b/,
  /\bgovernment\b/,
  /\bnonprofit\b/,
  /\btrade association\b/,
]

// Advertiser-name patterns that look like a candidate's authorized committee.
// Used as a fallback when affiliation type is missing or ambiguous.
const CANDIDATE_NAME_PATTERNS: RegExp[] = [
  // "<Name> for <Office/State>" — "Steyer for CA Governor", "Smith for Senate", "Jones for Congress"
  /^[a-z][a-z'.\-]+(?:\s+[a-z'.\-]+){0,3}\s+for\s+(?:[a-z]{2,}\s+)?(?:senate|governor|congress|house|attorney general|treasurer|secretary of state|lieutenant governor|state\s+\w+|u\.?s\.?\s+\w+|america|the\s+\w+)\b/,
  // "Friends of <Name>"
  /^friends of\s+[a-z]/,
  // "Committee to (Re-?)Elect <Name>"
  /^committee to\s+(?:re-?)?elect\s+[a-z]/,
  // "<Name> for <Year>" — "Smith for 2026"
  /^[a-z][a-z'.\-]+(?:\s+[a-z'.\-]+){0,3}\s+for\s+20\d{2}\b/,
  // "Re-?Elect <Name>"
  /^re-?elect\s+[a-z]/,
  // "<Name> Victory Committee" / "<Name> for America"
  /\bfor america\b/,
  /\bvictory (?:committee|fund)\b/,
]

function classifyCandidate(
  advertiser: string,
  affiliationType: string,
): { isCandidate: boolean; classification: SpendRow['candidateClassification'] } {
  const normAffiliation = normalizeText(affiliationType)
  const normName = normalizeText(advertiser)

  if (normAffiliation) {
    if (NON_CANDIDATE_AFFILIATION_PATTERNS.some((re) => re.test(normAffiliation))) {
      return { isCandidate: false, classification: 'non-candidate' }
    }
    if (CANDIDATE_AFFILIATION_PATTERNS.some((re) => re.test(normAffiliation))) {
      const isJoint = /candidate\s*\/\s*pac|candidate pac/.test(normAffiliation)
      return {
        isCandidate: true,
        classification: isJoint ? 'candidate-pac' : 'candidate-committee',
      }
    }
  }

  // Fall back to advertiser-name heuristics when affiliation type is absent
  // or unhelpful (e.g., "Unknown", blank, or generic "PAC").
  if (normName && CANDIDATE_NAME_PATTERNS.some((re) => re.test(normName))) {
    return { isCandidate: true, classification: 'name-pattern' }
  }

  return { isCandidate: false, classification: 'non-candidate' }
}

// Extract a likely candidate display name from an advertiser string.
// "Steyer for CA Governor" -> "Steyer"
// "Friends of John Smith" -> "John Smith"
// "Committee to Elect Jane Doe" -> "Jane Doe"
// Otherwise return the advertiser as-is.
function deriveCandidateName(advertiser: string): string {
  const trimmed = advertiser.trim()
  if (!trimmed) return ''

  const friendsOf = trimmed.match(/^friends of\s+(.+)$/i)
  if (friendsOf) return friendsOf[1].trim()

  const elect = trimmed.match(/^committee to\s+(?:re-?)?elect\s+(.+)$/i)
  if (elect) return elect[1].trim()

  const reelect = trimmed.match(/^re-?elect\s+(.+?)(?:\s+for\s+.+)?$/i)
  if (reelect) return reelect[1].trim()

  const forRace = trimmed.match(/^(.+?)\s+for\s+.+$/i)
  if (forRace) return forRace[1].trim()

  return trimmed
}

// Sum any per-bucket spend columns the workbook exposes directly so that a
// single advertiser row can produce one SpendRow per non-zero bucket.
const BUCKET_COLUMN_ALIASES: Array<{ bucket: MediaBucket; aliases: string[] }> = [
  { bucket: 'broadcast', aliases: ['broadcast'] },
  { bucket: 'cable', aliases: ['cable'] },
  { bucket: 'ctv', aliases: ['ctv', 'streaming', 'ott'] },
  { bucket: 'digital', aliases: ['digital', 'online', 'display'] },
  { bucket: 'other', aliases: ['radio'] },
]

export function parseSpendRows(rows: Record<string, unknown>[]): SpendRow[] {
  const output: SpendRow[] = []
  let counter = 0

  // If the workbook uses a "Candidate" / "Supported Candidate" header instead
  // of an "Advertiser" header, the upstream data has already labeled the row
  // as belonging to a candidate — skip the affiliation/name classification.
  const candidateColumnAliases = ['candidate', 'supported candidate']
  const advertiserColumnAliases = [
    'advertiser',
    'downballot advertiser',
    'governor advertiser',
    'senate advertiser',
    'house advertiser',
  ]

  for (const row of rows) {
    const candidateKey = lookupKey(row, candidateColumnAliases)
    const advertiserKey = candidateKey ?? lookupKey(row, [...advertiserColumnAliases, 'name'])
    const sourceIsCandidateColumn = Boolean(candidateKey)
    const stateKey = lookupKey(row, ['verified state', 'state', 'province'])
    const officeKey = lookupKey(row, ['race type', 'office', 'race', 'contest'])
    const districtKey = lookupKey(row, ['district', 'county'])
    const mediaKey = lookupKey(row, ['media type', 'media', 'channel', 'platform'])
    const spendKey = lookupKey(row, ['grand total', 'total spend', 'total', 'spend', 'amount', 'gross', 'cost'])
    const affiliationTypeKey = lookupKey(row, ['affiliation type'])
    const partyKey = lookupKey(row, ['party affiliation', 'party'])

    const advertiserName = String(advertiserKey ? row[advertiserKey] : '').trim()
    if (!advertiserName) continue

    const affiliationType = String(affiliationTypeKey ? row[affiliationTypeKey] : '').trim()
    const partyAffiliation = String(partyKey ? row[partyKey] : '').trim()
    const state = String(stateKey ? row[stateKey] : '').trim().toUpperCase()
    const office = String(officeKey ? row[officeKey] : '').trim()
    const district = String(districtKey ? row[districtKey] : '').trim()

    const classified = sourceIsCandidateColumn
      ? { isCandidate: true, classification: 'candidate-committee' as const }
      : classifyCandidate(advertiserName, affiliationType)
    const { isCandidate, classification } = classified
    const candidateName =
      isCandidate && !sourceIsCandidateColumn
        ? deriveCandidateName(advertiserName)
        : advertiserName

    const baseRow = {
      advertiserName,
      candidateName,
      normalizedCandidate: normalizeCandidate(candidateName),
      state,
      office,
      district,
      affiliationType,
      partyAffiliation,
      isCandidate,
      candidateClassification: classification,
      sourceRow: row,
    }

    // Prefer per-bucket columns when present; otherwise fall back to
    // the legacy single media-type + spend pair.
    const perBucketEntries: Array<{ bucket: MediaBucket; raw: string; spend: number }> = []
    for (const { bucket, aliases } of BUCKET_COLUMN_ALIASES) {
      const key = lookupKey(row, aliases)
      if (!key) continue
      const value = parseSpend(row[key])
      if (value > 0) {
        perBucketEntries.push({ bucket, raw: key, spend: value })
      }
    }

    if (perBucketEntries.length > 0) {
      for (const entry of perBucketEntries) {
        counter += 1
        output.push({
          id: `spend-${counter}`,
          ...baseRow,
          mediaTypeRaw: entry.raw,
          mediaBucket: entry.bucket,
          spend: entry.spend,
        })
      }
      continue
    }

    const mediaTypeRaw = String(mediaKey ? row[mediaKey] : '').trim()
    const spend = parseSpend(spendKey ? row[spendKey] : 0)
    if (!mediaTypeRaw || !spend) continue

    counter += 1
    output.push({
      id: `spend-${counter}`,
      ...baseRow,
      mediaTypeRaw,
      mediaBucket: classifyMediaBucket(mediaTypeRaw),
      spend,
    })
  }

  return output
}
