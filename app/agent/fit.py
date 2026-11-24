"""Job-description fit engine.

Compares a job description against the owner's stored profile (skills,
projects, summary) and produces:

  extracted   - what the JD actually asks for (transparent phrase matching:
                every reported skill points at words in the JD text)
  matched     - JD requirements the profile already covers, and where
  missing     - JD requirements absent from the profile (the prep list)
  similarity  - embedding cosine between the JD and the profile summary.
                A semantic-similarity signal, not a verdict: keyword
                overlap drives matched/missing, this is the second opinion.

Fully deterministic; an LLM, when configured, only narrates the result.
"""

import json
import sqlite3

from .. import db
from ..nlp import extract as jd_extract
from ..nlp import skills as skill_lex

# Skill -> question-bank category, so a gap can route straight to practice.
_SKILL_TO_CATEGORY = {
    "Python": "python", "Java": "cs", "JavaScript": "web", "TypeScript": "web",
    "SQL": "sql", "PostgreSQL": "sql", "MySQL": "sql", "SQLite": "sql",
    "MongoDB": "sql", "Redis": "sql",
    "React": "web", "Next.js": "web", "Vue": "web", "Angular": "web",
    "HTML": "web", "CSS": "web", "Node.js": "web", "FastAPI": "python",
    "Django": "python", "Flask": "python",
    "Machine Learning": "ml", "Deep Learning": "ml", "NLP": "ml",
    "TensorFlow": "ml", "PyTorch": "ml", "scikit-learn": "ml",
    "LLM": "ml", "LLMs": "ml", "RAG": "ml", "Transformers": "ml",
    "Data Structures": "dsa", "Algorithms": "dsa", "System Design": "cs",
    "OOP": "cs", "Design Patterns": "cs", "Operating Systems": "cs",
    "Docker": "cs", "Kubernetes": "cs", "Git": "cs", "CI/CD": "cs",
}


def load_profile(conn: sqlite3.Connection) -> dict:
    fields = {r["key"]: r["value"] for r in
              conn.execute("SELECT key, value FROM profile_field")}
    skills = db.query(conn, "SELECT * FROM skill")
    projects = db.query(conn, "SELECT * FROM project")
    for p in projects:
        p["tech"] = json.loads(p["tech"] or "[]")
        p["highlights"] = json.loads(p["highlights"] or "[]")
    return {"fields": fields, "skills": skills, "projects": projects}


def _profile_skill_names(profile: dict) -> set[str]:
    names = {s["name"] for s in profile["skills"]}
    for p in profile["projects"]:
        names.update(p["tech"])
    # normalise through the lexicon aliases so "Node" matches "Node.js"
    return names


def _canon(name: str) -> str:
    return name.strip().lower()


def profile_text(profile: dict) -> str:
    parts = [profile["fields"].get("headline", ""), profile["fields"].get("summary", "")]
    parts += [s["name"] for s in profile["skills"]]
    for p in profile["projects"]:
        parts.append(p["title"])
        parts.append(p["description"])
        parts.extend(p["tech"])
    return " ".join(x for x in parts if x)


def _similarity(jd_text: str, profile: dict) -> float | None:
    """Embedding cosine JD vs profile text. None when embeddings unavailable."""
    try:
        from ..nlp import embed
        vecs = embed.embed_texts([jd_text[:4000], profile_text(profile)[:4000]])
        a, b = vecs[0], vecs[1]
        denom = (float((a ** 2).sum()) ** 0.5) * (float((b ** 2).sum()) ** 0.5)
        return round(float((a * b).sum()) / denom, 3) if denom else None
    except Exception:
        return None


def _suggestions(missing: list[str]) -> list[dict]:
    out = []
    for skill in missing[:5]:
        cat = _SKILL_TO_CATEGORY.get(skill, "")
        if cat:
            out.append({
                "skill": skill, "kind": "practice",
                "text": f"Practice {cat} questions tagged '{skill}' in Interview mode.",
                "category": cat,
            })
        else:
            out.append({
                "skill": skill, "kind": "study",
                "text": f"Skim the official {skill} docs and build one tiny demo you can talk about.",
                "category": "",
            })
    return out


def analyze(conn: sqlite3.Connection, jd_text: str, application_id: int | None = None) -> dict:
    steps: list[dict] = []
    profile = load_profile(conn)
    steps.append({"step": "load_profile",
                  "detail": f"Loaded {len(profile['skills'])} skill(s) and "
                            f"{len(profile['projects'])} project(s) from your profile."})

    extracted = jd_extract.extract_all(jd_text)
    jd_skills_grouped = skill_lex.extract_skills(jd_text)
    jd_skills = skill_lex.flat_skills(jd_skills_grouped)
    steps.append({"step": "extract_jd",
                  "detail": f"Found {len(jd_skills)} skill(s) in the job description by phrase match."})

    have = {_canon(n) for n in _profile_skill_names(profile)}
    matched, missing = [], []
    for skill in jd_skills:
        if _canon(skill) in have:
            where = [s["evidence"] for s in profile["skills"]
                     if _canon(s["name"]) == _canon(skill) and s["evidence"]]
            matched.append({"skill": skill, "evidence": where[0] if where else "profile"})
        else:
            missing.append(skill)
    steps.append({"step": "compare",
                  "detail": f"{len(matched)} requirement(s) covered by your profile, "
                            f"{len(missing)} gap(s)."})

    sim = _similarity(jd_text, profile)
    if sim is not None:
        steps.append({"step": "similarity",
                      "detail": f"Embedding similarity between the JD and your profile: {sim}."})

    suggestions = _suggestions(missing)
    result = {
        "extracted": extracted,
        "jd_skills": jd_skills,
        "matched": matched,
        "missing": missing,
        "similarity": sim,
        "suggestions": suggestions,
    }
    analysis_id = db.insert(conn, "jd_analysis", {
        "application_id": application_id,
        "jd_text": jd_text,
        "extracted": json.dumps(extracted, ensure_ascii=False),
        "matched": json.dumps(matched, ensure_ascii=False),
        "missing": json.dumps(missing, ensure_ascii=False),
        "similarity": sim,
        "suggestions": json.dumps(suggestions, ensure_ascii=False),
        "llm_used": 0,
        "created_at": db.now(),
    })
    run_id = db.record_agent_run(
        conn, "fit", {"application_id": application_id, "jd_chars": len(jd_text)},
        steps, {"matched": len(matched), "missing": missing})
    result.update({"analysis_id": analysis_id, "agent_run_id": run_id, "steps": steps})
    return result
