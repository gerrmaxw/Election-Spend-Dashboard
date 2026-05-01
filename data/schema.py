from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class MediaType(str, Enum):
    BROADCAST = "broadcast_tv"
    CABLE = "cable"
    CTV = "ctv"
    DIGITAL = "digital"
    RADIO = "radio"
    MAIL = "mail"
    OTHER = "other"


class OfficeType(str, Enum):
    US_SENATE = "us_senate"
    US_HOUSE = "us_house"
    GOVERNOR = "governor"
    ATTORNEY_GENERAL = "attorney_general"
    STATE_SENATE = "state_senate"
    STATE_HOUSE = "state_house"
    OTHER = "other"


class RaceResult(str, Enum):
    WON = "won"
    LOST = "lost"
    PENDING = "pending"


class Race(BaseModel):
    race_id: str
    cycle: int
    office: OfficeType
    state: str
    district: Optional[str] = None
    general_date: Optional[date] = None
    election_name: Optional[str] = None
    election_scope: Optional[str] = None
    election_type: Optional[str] = None
    source: str = "civicapi"


class Candidate(BaseModel):
    candidate_id: str
    full_name: str
    party: Optional[str] = None
    race_id: str
    incumbent: bool = False
    result: RaceResult = RaceResult.PENDING
    votes_received: Optional[int] = None
    vote_share: Optional[float] = None
    margin: Optional[float] = None


class SpendRecord(BaseModel):
    spend_id: str
    source_batch_id: str
    source_file: str
    advertiser_name: str
    candidate_id: str
    race_id: str
    media_type: MediaType
    gross_amount: float
    match_source: str = "unmatched"
    match_reason: Optional[str] = None
    match_confidence: float = 0.0
    likely_state: Optional[str] = None
    likely_office: Optional[str] = None
    likely_surname: Optional[str] = None
    agency: Optional[str] = None
    is_national: int = 0   # 1 = source row had State="National" (PAC/outside group)
    source_data_type: Optional[str] = None
    source_race_type: Optional[str] = None
    source_state: Optional[str] = None
    source_district: Optional[str] = None
    source_party_affiliation: Optional[str] = None
    advertiser_type: Optional[str] = None
