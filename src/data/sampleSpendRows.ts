import { parseSpendRows } from '../lib/spendParser'

const SAMPLE_SPEND_UPLOAD = [
  { Candidate: 'Maya Brooks', State: 'GA', Office: 'House', 'Media Type': 'Cable Net', Spend: 420000 },
  { Candidate: 'Maya Brooks', State: 'GA', Office: 'House', 'Media Type': 'CTV', Spend: 185000 },
  { Candidate: 'Evan Holt', State: 'GA', Office: 'House', 'Media Type': 'Local Spot Broadcast', Spend: 510000 },
  { Candidate: 'Lena Torres', State: 'TX', Office: 'Senate', 'Media Type': 'Broadcast Network', Spend: 1350000 },
  { Candidate: 'Caleb Warren', State: 'TX', Office: 'Senate', 'Media Type': 'Broadcast Network', Spend: 840000 },
  { Candidate: 'Caleb Warren', State: 'TX', Office: 'Senate', 'Media Type': 'Cable Net', Spend: 610000 },
  { Candidate: 'Nora Patel', State: 'MI', Office: 'Governor', 'Media Type': 'Comcast Cable', Spend: 920000 },
  { Candidate: 'Ryan McCall', State: 'MI', Office: 'Governor', 'Media Type': 'Local Spot Broadcast', Spend: 780000 },
  { Candidate: 'Dana Kim', State: 'PA', Office: 'House', 'Media Type': 'Broadcast OTA', Spend: 455000 },
  { Candidate: 'Chris Mercer', State: 'PA', Office: 'House', 'Media Type': 'CTV', Spend: 240000 },
  { Candidate: 'Chris Mercer', State: 'PA', Office: 'House', 'Media Type': 'Cable Net', Spend: 370000 },
  { Candidate: 'Olivia Reed', State: 'VA', Office: 'Attorney General', 'Media Type': 'CTV', Spend: 310000 },
  { Candidate: 'Olivia Reed', State: 'VA', Office: 'Attorney General', 'Media Type': 'Cable Net', Spend: 460000 },
  { Candidate: 'Marcus Dale', State: 'VA', Office: 'Attorney General', 'Media Type': 'Broadcast Network', Spend: 520000 },
]

export const SAMPLE_SPEND_ROWS = parseSpendRows(SAMPLE_SPEND_UPLOAD)
