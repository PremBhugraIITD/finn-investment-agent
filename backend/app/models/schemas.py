"""Typed contracts shared by services and API."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Category = Literal[
    "results", "M&A", "fund_raise", "management_change", "litigation",
    "insider_trading", "earnings_call", "investor_meetings", "credit_rating",
    "dividend", "bonus_split", "capex", "press_release", "postal_ballot",
    "agm_egm", "pledging", "unclassified",
]


class FinnError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


# ---------------- Tier-1 structured outputs (OpenAI Structured Outputs) ----------------
class ClassifiedItem(BaseModel):
    id: str
    category: Category
    confidence: float
    summary: str = Field(description="One factual line, max 22 words, no hype.")
    amount_inr_cr: Optional[float] = Field(default=None, description="Headline INR crore amount if stated, else null.")
    severity: Literal["low", "medium", "high"]
    direction: Literal["positive", "negative", "neutral"]


class ClassifiedBatch(BaseModel):
    items: list[ClassifiedItem]


class Figure(BaseModel):
    current: Optional[float] = None
    prev_quarter: Optional[float] = None
    year_ago: Optional[float] = None
    derived: bool
    page: Optional[int] = None
    quote: Optional[str] = None


class ResultsExtraction(BaseModel):
    quarter_label: str = Field(description="e.g. Q2 FY27 or Quarter ended 30 Sep 2026")
    unit: str = Field(description="Unit as printed, e.g. INR crore, INR lakh, INR million")
    basis: Literal["consolidated", "standalone", "unknown"]
    revenue: Figure
    ebitda: Figure
    pbt: Figure
    pat: Figure


class Person(BaseModel):
    name: str
    role: str
    action: Literal["joining", "departing", "other"]
    effective_date: Optional[str] = None
    background_in_filing: Optional[str] = Field(default=None, description="Background stated in the filing only.")
    page: Optional[int] = None


class MgmtExtraction(BaseModel):
    people: list[Person]


class BioDraft(BaseModel):
    bio: str = Field(description="2-3 lines. Only facts present in the supplied text.")
    previous_roles: list[str]
    confidence: Literal["low", "medium", "high"]


# ---------------- API-facing ----------------
class Settings(BaseModel):
    price_move_pct: float = 3.0
    volume_ratio: float = 2.0
    materiality: int = 70
    categories: list[str] = Field(default_factory=lambda: [
        "results", "M&A", "fund_raise", "management_change", "litigation",
        "credit_rating", "capex", "pledging",
    ])


# ---------------- Pull-mode structured outputs ----------------
class GuidanceClaim(BaseModel):
    metric: Literal["revenue_growth_yoy", "ebitda_margin", "pat_growth_yoy", "capex_inr_cr", "other"]
    description: str = Field(description="What management said, in under 25 words.")
    low: Optional[float] = Field(description="Lower bound as a plain number (percent points or INR crore), else null.")
    high: Optional[float] = Field(description="Upper bound, else null.")
    unit: Literal["pct", "inr_cr", "other"]
    period_type: Literal["next_quarter", "current_fy", "next_fy", "medium_term", "unspecified"]
    explicit: bool = Field(description="True only if management gave a specific numeric target or range.")
    quote: str = Field(description="Verbatim sentence(s) from the transcript supporting the claim, max 45 words.")
    page: int


class GuidanceExtraction(BaseModel):
    claims: list[GuidanceClaim]


class YearFin(BaseModel):
    fy: str = Field(description="Fiscal year label like FY25")
    revenue: Optional[float] = None
    ebitda: Optional[float] = None
    pbt: Optional[float] = None
    pat: Optional[float] = None
    total_debt: Optional[float] = None
    net_worth: Optional[float] = None
    cfo: Optional[float] = Field(default=None, description="Net cash from operating activities")
    capex: Optional[float] = Field(default=None, description="Purchase of PPE / capital expenditure, as a positive number")
    receivable_days: Optional[float] = None
    inventory_days: Optional[float] = None
    payable_days: Optional[float] = None
    page: Optional[int] = None
    quote: Optional[str] = Field(default=None, description="Short verbatim snippet from the table row supporting revenue or PAT")


class FinSeries(BaseModel):
    unit: str = Field(description="Exactly one of: INR crore, INR lakh, INR million, INR billion")
    basis: Literal["consolidated", "standalone", "unknown"]
    years: list[YearFin]


class DeepCite(BaseModel):
    ref: str = Field(description="Excerpt id like C3 or fact id like M7")
    quote: str = Field(description="For C ids: a verbatim snippet from that excerpt (max 30 words). For M ids: empty string.")


class DeepClaim(BaseModel):
    text: str
    stance: Literal["bull", "bear", "none"]
    cites: list[DeepCite]


class DeepSection(BaseModel):
    id: Literal["business", "financial", "trajectory", "balance_sheet", "cash_quality", "narrative", "bull_bear"]
    claims: list[DeepClaim]
    gap: Optional[str] = Field(description="If the evidence is insufficient: what is missing. Else null.")


class DeepOutput(BaseModel):
    sections: list[DeepSection]
    scorecard_takeaways: list[DeepClaim]


class AskOutput(BaseModel):
    claims: list[DeepClaim]
