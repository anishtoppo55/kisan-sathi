"""Yojana Saathi: scheme Q&A (RAG) and the rule-based eligibility checker."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

SCHEMES_PATH = Path(__file__).resolve().parents[2] / "kb" / "schemes.json"

Status = Literal["eligible", "not_eligible", "needs_check", "already_enrolled"]


class FarmerProfile(BaseModel):
    """Everything optional: unknown fields lead to 'needs_check', never a guess."""

    owns_land: bool | None = None
    land_acres: float | None = Field(default=None, ge=0, le=10000)
    is_tenant: bool | None = None
    income_tax_payer: bool | None = None
    govt_employee_or_pensioner_10k: bool | None = None
    grows_paddy: bool | None = None
    registered_on_state_portal: bool | None = None
    has_kcc: bool | None = None
    district: str | None = Field(default=None, max_length=40)

    def merged(self, other: "FarmerProfile") -> "FarmerProfile":
        """Values in `other` override ours when they are not None."""
        data = self.model_dump()
        data.update({k: v for k, v in other.model_dump().items() if v is not None})
        return FarmerProfile(**data)


# JSON schema for Gemini slot extraction (subset of FarmerProfile)
PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "owns_land": {"type": ["boolean", "null"], "description": "Farmer owns farmland in own name"},
        "land_acres": {"type": ["number", "null"], "description": "Land in acres (1 hectare = 2.47 acres)"},
        "is_tenant": {"type": ["boolean", "null"], "description": "Farms someone else's land: tenant, lessee, sharecropper (batai/adhiya)"},
        "income_tax_payer": {"type": ["boolean", "null"]},
        "govt_employee_or_pensioner_10k": {"type": ["boolean", "null"], "description": "Government employee, or pension >= Rs 10,000/month"},
        "grows_paddy": {"type": ["boolean", "null"]},
        "registered_on_state_portal": {"type": ["boolean", "null"], "description": "Registered on Chhattisgarh Integrated Farmer Portal / cooperative society for paddy sale"},
        "has_kcc": {"type": ["boolean", "null"], "description": "Already has a Kisan Credit Card"},
        "district": {"type": ["string", "null"], "description": "Chhattisgarh district name in English, e.g. Raipur, Bemetara"},
    },
}

EXTRACT_SYSTEM = """Extract a farmer's profile from what they said (Hindi, Hinglish or English).
Only fill a field when the farmer clearly stated it; otherwise use null. Never guess.
Examples: "income tax nahi bharta" -> income_tax_payer=false. "2 acre ka kisan hoon" -> owns_land=true, land_acres=2.
"batai par kheti" -> is_tenant=true. "KCC nahi hai" -> has_kcc=false. "dhaan bota hoon" -> grows_paddy=true."""


def load_schemes(path: Path = SCHEMES_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["schemes"]


def _loc(d, lang: str):
    """Pick the language variant; en-IN and unknown fall back to Hinglish, then Hindi."""
    if not isinstance(d, dict):
        return d
    return d.get(lang) or d.get("hi-en") or d.get("hi-IN")


def _eval_simple(check: dict, p: FarmerProfile) -> bool | None:
    value = getattr(p, check["field"])
    return None if value is None else value == check["is"]


def eval_check(check: dict, p: FarmerProfile) -> bool | None:
    if "any" in check:
        results = [_eval_simple(c, p) for c in check["any"]]
        if any(r is True for r in results):
            return True
        if all(r is False for r in results):
            return False
        return None
    return _eval_simple(check, p)


def check_scheme(scheme: dict, p: FarmerProfile, lang: str = "hi-en") -> dict:
    fails, asks = [], []
    for check in scheme["checks"]:
        r = eval_check(check, p)
        if r is False:
            fails.append(_loc(check["fail"], lang))
        elif r is None:
            asks.append(_loc(check["ask"], lang))
    if fails:
        status: Status = "not_eligible"
    elif scheme.get("already_field") and getattr(p, scheme["already_field"]) is True:
        status = "already_enrolled"
    elif asks:
        status = "needs_check"
    else:
        status = "eligible"
    return {
        "id": scheme["id"],
        "name": _loc(scheme["name"], lang),
        "status": status,
        "reasons": fails,
        "questions": asks if status == "needs_check" else [],
        "benefit": _loc(scheme["benefit"], lang),
        "documents": _loc(scheme["documents"], lang),
        "notes": _loc(scheme.get("notes", {}), lang) or [],
        "link": scheme["link"],
    }


def check_all(schemes: list[dict], p: FarmerProfile, lang: str = "hi-en") -> list[dict]:
    order = {"eligible": 0, "already_enrolled": 1, "needs_check": 2, "not_eligible": 3}
    results = [check_scheme(s, p, lang) for s in schemes]
    return sorted(results, key=lambda r: order[r["status"]])


def scheme_card(scheme: dict, lang: str) -> dict:
    return {
        "type": "scheme",
        "data": {
            "id": scheme["id"],
            "title": _loc(scheme["name"], lang),
            "benefit": _loc(scheme["benefit"], lang),
            "documents": _loc(scheme["documents"], lang),
            "apply": _loc(scheme["apply"], lang),
            "helpline": scheme.get("helpline"),
            "link": scheme["link"],
        },
    }


def eligibility_card(results: list[dict], profile: FarmerProfile) -> dict:
    return {"type": "eligibility", "data": {"results": results, "profile": profile.model_dump()}}


def match_schemes(text: str, schemes: list[dict], hit_scheme_ids: list[str]) -> list[dict]:
    """Schemes named in the question (keywords), else the scheme of the top retrieved chunk."""
    low = text.lower()
    named = [s for s in schemes if any(k in low for k in s["keywords"])]
    if named:
        return named[:2]
    by_id = {s["id"]: s for s in schemes}
    return [by_id[hit_scheme_ids[0]]] if hit_scheme_ids and hit_scheme_ids[0] in by_id else []


SUMMARY = {
    "hi-IN": {"eligible": "आप इन योजनाओं के लिए पात्र लगते हैं: {names}।", "needs_check": "इनके लिए थोड़ी और जानकारी चाहिए: {names}।",
              "not_eligible": "इनके लिए आप पात्र नहीं लगते: {names}।", "already_enrolled": "आपके पास पहले से है: {names}।",
              "tail": "पूरी जानकारी कार्ड में देखें और नज़दीकी कृषि कार्यालय से पुष्टि करें।"},
    "hi-en": {"eligible": "Aap in yojanaon ke liye paatra lagte hain: {names}.", "needs_check": "Inke liye thodi aur jaankari chahiye: {names}.",
              "not_eligible": "Inke liye aap paatra nahi lagte: {names}.", "already_enrolled": "Aapke paas pehle se hai: {names}.",
              "tail": "Poori jaankari card mein dekhein aur nazdeeki krishi karyalay se pushti karein."},
}


def summarize(results: list[dict], lang: str) -> str:
    t = SUMMARY.get(lang, SUMMARY["hi-en"])
    parts = []
    for status in ("eligible", "already_enrolled", "needs_check", "not_eligible"):
        names = [r["name"] for r in results if r["status"] == status]
        if names:
            parts.append(t[status].format(names=", ".join(names)))
    parts.append(t["tail"])
    return " ".join(parts)
