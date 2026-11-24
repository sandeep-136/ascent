"""Transparent field extraction from job posting text.

Every extractor is a documented pattern, not a black box: results include
the matched text so the UI can show exactly why a field was detected.
"""

import re

_SALARY = re.compile(
    r"(?P<full>(?:₹|rs\.?|inr|\$|usd)\s?[\d,]+(?:\.\d+)?\s*(?:k|l|lakh(?:s)?|lpa|per\s+(?:annum|year|month|hour))?"
    r"(?:\s*(?:-|–|to)\s*(?:₹|rs\.?|inr|\$|usd)?\s?[\d,]+(?:\.\d+)?\s*(?:k|l|lakh(?:s)?|lpa|per\s+(?:annum|year|month|hour))?)?"
    r"|[\d.]+\s*(?:-|–|to)\s*[\d.]+\s*(?:lakh(?:s)?|lpa)(?:\s*(?:per\s+annum|p\.?a\.?))?"
    r"|[\d.]+\s*(?:lakh(?:s)?|lpa)(?:\s*(?:per\s+annum|p\.?a\.?))?)",
    re.IGNORECASE,
)

_EXPERIENCE = re.compile(
    r"(?P<full>(?:\d+\s*(?:-|–|to)\s*\d+|\d+\s*\+?)\s*(?:years?|yrs?)(?:\s+of\s+(?:relevant\s+)?(?:experience|exp))?)",
    re.IGNORECASE,
)

_EDUCATION_TERMS = [
    "Ph.D", "PhD", "Doctorate", "Master's degree", "Master's", "M.Tech", "M.Tech.",
    "MSc", "M.Sc", "MBA", "MCA", "Bachelor's degree", "Bachelor's", "B.E.", "B.E",
    "B.Tech", "B.Tech.", "BTech", "BSc", "B.Sc", "BCA", "BBA", "Diploma",
    "degree in Computer Science", "Computer Science degree", "Engineering degree",
]

_EMPLOYMENT_TYPES = {
    "Full-time": [r"full[\s-]?time"],
    "Part-time": [r"part[\s-]?time"],
    "Internship": [r"internship", r"\bintern\b", r"\binterns\b", r"industrial training"],
    "Contract": [r"\bcontract\b", r"contractual", r"\bfreelance\b"],
    "Temporary": [r"\btemporary\b", r"\btemp\b"],
}

_WORK_MODES = {
    "Remote": [r"\bremote\b", r"work from home", r"\bwfh\b", r"anywhere"],
    "Hybrid": [r"\bhybrid\b"],
    "On-site": [r"on[\s-]?site", r"in[\s-]?office", r"from (our|the) office"],
}

# Wording patterns commonly seen in fraudulent postings. These are heuristic
# red flags for the reader to check, not proof of fraud - a real company can
# use one of these phrases, and a scam can use none of them.
RED_FLAG_PATTERNS = {
    "Asks for money": [
        r"registration fee", r"security deposit", r"refundable deposit",
        r"training fee", r"processing fee", r"pay.{0,40}(to apply|to join|for (the|this) (job|training|registration))",
        r"(fee|deposit|charge).{0,30}(refundable|registration|training)",
    ],
    "Too-good pay promises": [
        r"earn.{0,30}(per day|daily|weekly).{0,20}(home|guaranteed)?",
        r"(guaranteed|unlimited) (income|earnings?)",
        r"no experience (needed|required).{0,40}(earn|income|\$|₹)",
        r"work (only )?\d+\s*(?:-|to)?\s*\d*\s*hours?.{0,30}(earn|income)",
    ],
    "Off-platform contact": [
        r"(contact|apply|send|whatsapp|message).{0,30}(whatsapp|telegram)",
        r"(whatsapp|telegram).{0,30}(number|only|:)",
        r"interview.{0,30}(whatsapp|telegram|hangouts|signal)",
    ],
    "Sensitive data requests": [
        r"(share|send|provide).{0,30}(bank (account|details)|credit card|debit card|otp|aadhaar|pan card)",
        r"(gift card|western union|moneygram|money gram)",
    ],
    "Pressure tactics": [
        r"urgent(ly)? (hiring|required|opening).{0,30}(limited|immediately|today)",
        r"limited (seats|positions|slots).{0,20}(apply|hurry)",
        r"100% (job )?(guarantee|placement)",
    ],
}


def extract_salary(text: str) -> list[str]:
    seen, out = set(), []
    for m in _SALARY.finditer(text):
        val = re.sub(r"\s+", " ", m.group("full")).strip(" ,.-")
        key = val.lower()
        if len(val) > 3 and key not in seen:
            seen.add(key)
            out.append(val)
    return out[:4]


def extract_experience(text: str) -> list[str]:
    seen, out = set(), []
    for m in _EXPERIENCE.finditer(text):
        val = m.group("full").strip()
        key = val.lower()
        if key not in seen:
            seen.add(key)
            out.append(val)
    return out[:3]


def extract_education(text: str) -> list[str]:
    lowered = text.lower()
    found = []
    for term in _EDUCATION_TERMS:
        if term.lower() in lowered and term.strip(".") not in [f.strip(".") for f in found]:
            found.append(term)
    return found[:5]


def extract_employment_types(text: str) -> list[str]:
    return [label for label, pats in _EMPLOYMENT_TYPES.items()
            if any(re.search(p, text, re.IGNORECASE) for p in pats)]


def extract_work_modes(text: str) -> list[str]:
    return [label for label, pats in _WORK_MODES.items()
            if any(re.search(p, text, re.IGNORECASE) for p in pats)]


def extract_red_flags(text: str) -> list[dict]:
    flags = []
    for category, pats in RED_FLAG_PATTERNS.items():
        for p in pats:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                flags.append({"category": category, "matched_text": m.group(0).strip()})
                break
    return flags


_ACTION_START = re.compile(
    r"^(develop|design|build|write|maintain|collaborate|work|lead|own|drive|create|implement|"
    r"manage|analyze|analyse|test|deploy|improve|optimize|optimise|support|research|ensure|"
    r"participate|contribute|debug|review|mentor|architect|deliver|define|establish|monitor)",
    re.IGNORECASE,
)


def extract_responsibilities(text: str, limit: int = 6) -> list[str]:
    """Pull bullet-like lines and action-verb sentences as likely responsibilities."""
    candidates = []
    for raw in re.split(r"\n+", text):
        line = re.sub(r"^[\s•\-\*\u2022\u25cf\d\.\)]+", "", raw).strip()
        if 40 <= len(line) <= 220 and _ACTION_START.match(line):
            candidates.append(line)
    # de-dup while keeping order
    seen, out = set(), []
    for c in candidates:
        key = c.lower()[:60]
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out[:limit]


def extract_title_hint(text: str) -> str | None:
    """First short line that looks like a job title (used for display only)."""
    for raw in re.split(r"\n+", text.strip()):
        line = raw.strip().strip(":")
        if 4 <= len(line) <= 70 and not line.lower().startswith(("about", "we are", "job description", "role", "responsibilities", "requirements", "qualifications")):
            words = line.split()
            if len(words) <= 8 and any(w[0].isupper() for w in words if w):
                return line
        if len(line) > 120:
            break
    return None


def extract_all(text: str) -> dict:
    return {
        "title_hint": extract_title_hint(text),
        "salary_mentions": extract_salary(text),
        "experience_mentions": extract_experience(text),
        "education_mentions": extract_education(text),
        "employment_types": extract_employment_types(text),
        "work_modes": extract_work_modes(text),
        "red_flags": extract_red_flags(text),
        "responsibilities": extract_responsibilities(text),
        "word_count": len(text.split()),
    }
