import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const DEFAULT_ALLOW_TYPES = new Set([
  'us senate',
  'senate',
  'us house',
  'house',
  'us representative',
  'governor',
  'attorney general',
  'state senate',
  'state house',
  'state assembly',
  'state representative',
  'state delegate',
  'house of delegates',
])

function normalizeCandidate(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function normalizeOfficeType(value: string) {
  return value.trim().toLowerCase()
}

function deriveOfficeLabel(value: string) {
  const office = normalizeOfficeType(value)

  if (office.includes('attorney general')) return 'Attorney General'
  if (office.includes('governor')) return 'Governor'
  if (office.includes('state senate')) return 'State Senate'
  if (
    office.includes('state house') ||
    office.includes('state assembly') ||
    office.includes('state representative') ||
    office.includes('state delegate') ||
    office.includes('house of delegates')
  ) {
    return 'State House'
  }
  if (office === 'us senate' || office === 'senate') return 'Senate'
  if (
    office === 'us house' ||
    office === 'house' ||
    office.includes('us representative')
  ) {
    return 'House'
  }

  return value || 'Race'
}

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'civic-api-proxy',
      configureServer(server) {
        server.middlewares.use('/api/civic/search', async (req, res) => {
          const url = new URL(req.url ?? '', 'http://localhost:5173')
          const states = (url.searchParams.get('states') ?? '')
            .split(',')
            .map((state) => state.trim().toUpperCase())
            .filter(Boolean)
          const year = Number(url.searchParams.get('year') ?? '2026')
          const limit = Number(url.searchParams.get('limit') ?? '250')
          const includePrimaries = url.searchParams.get('includePrimaries') === 'true'

          const startDate = `${year}-01-01`
          const endDate = `${year}-12-31`

          try {
            const statePayloads = await Promise.all(
              states.map(async (state) => {
                const searchParams = new URLSearchParams({
                  country: 'US',
                  province: state,
                  startDate,
                  endDate,
                  limit: String(limit),
                })

                const searchResponse = await fetch(
                  `https://www.civicapi.org/api/v2/race/search?${searchParams.toString()}`,
                )

                if (!searchResponse.ok) {
                  throw new Error(`Search failed for ${state}`)
                }

                const searchJson = (await searchResponse.json()) as {
                  races?: Array<{ id: number }>
                }

                const races = (searchJson.races ?? []).filter((race) => {
                  const electionType = String((race as Record<string, unknown>).election_type ?? '')
                    .trim()
                    .toLowerCase()
                  const raceType = normalizeOfficeType(
                    String((race as Record<string, unknown>).type ?? ''),
                  )

                  if (!includePrimaries && electionType && electionType !== 'general') {
                    return false
                  }

                  return DEFAULT_ALLOW_TYPES.has(raceType)
                })

                const details = await Promise.all(
                  races.map(async (race) => {
                    const detailResponse = await fetch(
                      `https://www.civicapi.org/api/v2/race/${race.id}?data=json`,
                    )

                    if (!detailResponse.ok) {
                      return []
                    }

                    const detailJson = (await detailResponse.json()) as {
                      election_name?: string
                      election_type?: string
                      election_scope?: string
                      election_date?: string
                      province?: string | null
                      district?: string | null
                      candidates?: Array<{
                        name: string
                        party: string
                        votes: number
                        percent: number
                        winner: boolean
                      }>
                    }

                    const rawCandidates = detailJson.candidates ?? []
                    const totalVotes = rawCandidates.reduce(
                      (sum, candidate) => sum + Number(candidate.votes ?? 0),
                      0,
                    )
                    const winnerVotes = Math.max(
                      0,
                      ...rawCandidates.map((candidate) => Number(candidate.votes ?? 0)),
                    )
                    const raceType = String((race as Record<string, unknown>).type ?? '')
                    const office = deriveOfficeLabel(raceType)

                    return rawCandidates
                      .filter((candidate) => {
                        const normalized = normalizeCandidate(candidate.name)
                        return normalized && !['write in', 'write in other', 'other'].includes(normalized)
                      })
                      .map((candidate) => {
                        const votes = Number(candidate.votes ?? 0)
                        const voteShare = totalVotes
                          ? votes / totalVotes
                          : Number(candidate.percent ?? 0) > 1
                            ? Number(candidate.percent ?? 0) / 100
                            : Number(candidate.percent ?? 0)

                        return {
                          raceId: String(race.id),
                          candidateName: candidate.name,
                          normalizedCandidate: normalizeCandidate(candidate.name),
                          electionName: detailJson.election_name ?? `${state} race ${race.id}`,
                          office,
                          electionScope: detailJson.election_scope ?? '',
                          state: detailJson.province ?? state,
                          district: detailJson.district ?? '',
                          electionDate: detailJson.election_date ?? `${year}-11-03`,
                          party: candidate.party,
                          votes,
                          voteShare,
                          winner: Boolean(candidate.winner) || (votes > 0 && votes === winnerVotes),
                        }
                      })
                  }),
                )

                return details.flat()
              }),
            )

            res.setHeader('Content-Type', 'application/json')
            res.end(
              JSON.stringify({
                source: `CivicAPI ${year} (${includePrimaries ? 'general + primary' : 'general only'})`,
                results: statePayloads.flat(),
              }),
            )
          } catch (error) {
            res.statusCode = 500
            res.setHeader('Content-Type', 'application/json')
            res.end(
              JSON.stringify({
                error:
                  error instanceof Error ? error.message : 'CivicAPI proxy could not complete.',
              }),
            )
          }
        })
      },
    },
  ],
})
