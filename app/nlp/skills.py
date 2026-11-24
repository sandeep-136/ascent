"""Curated skill lexicon and phrase matcher.

The matcher is deliberately transparent: a skill is reported only when its
name (or a listed alias) appears in the text as a standalone phrase. No
embeddings, no guessing - every extracted skill can be pointed to in the
source text, which is what makes the extraction explainable in an interview.
"""

import re

SKILL_GROUPS = {
    "Programming languages": [
        "Python", "JavaScript", "TypeScript", "Java", "C++", "C#", "C", "Go",
        "Rust", "Kotlin", "Swift", "PHP", "Ruby", "R", "Scala", "MATLAB", "Dart", "SQL",
    ],
    "Frontend": [
        "React", "Next.js", "Vue", "Nuxt", "Angular", "Svelte", "HTML", "CSS",
        "Tailwind", "Tailwind CSS", "Bootstrap", "Sass", "Redux", "Vite", "Webpack",
        "jQuery", "Material UI", "Chakra UI", "Framer Motion", "React Native", "Flutter",
    ],
    "Backend": [
        "Node.js", "Express", "Express.js", "FastAPI", "Django", "Flask", "Spring",
        "Spring Boot", ".NET", "ASP.NET", "Laravel", "Rails", "GraphQL", "REST API",
        "gRPC", "Microservices", "WebSockets", "OAuth", "JWT", "Celery", "Full Stack",
    ],
    "Data & ML": [
        "Machine Learning", "Deep Learning", "NLP", "Natural Language Processing",
        "Computer Vision", "TensorFlow", "PyTorch", "Keras", "scikit-learn", "XGBoost",
        "LightGBM", "Pandas", "NumPy", "SciPy", "Matplotlib", "Seaborn", "Plotly",
        "OpenCV", "Hugging Face", "Transformers", "LangChain", "LLM", "LLMs",
        "spaCy", "NLTK",
        "Generative AI", "GenAI", "RAG", "Prompt Engineering", "Fine-tuning",
        "Feature Engineering", "MLOps", "MLflow", "Data Analysis", "Data Science",
        "Statistics", "A/B Testing", "Recommender Systems", "Time Series",
        "Spark", "PySpark", "Hadoop", "Airflow", "ETL", "dbt", "Power BI", "Tableau",
        "Excel", "Looker", "Kafka", "Data Engineering", "BigQuery", "Snowflake",
    ],
    "Databases": [
        "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "DynamoDB", "Cassandra",
        "Elasticsearch", "Oracle", "SQL Server", "Firebase", "Supabase", "Neo4j",
        "MariaDB", "NoSQL",
    ],
    "Cloud & DevOps": [
        "AWS", "Azure", "GCP", "Google Cloud", "Docker", "Kubernetes", "Terraform",
        "CI/CD", "Jenkins", "GitHub Actions", "GitLab CI", "Linux", "Bash", "Nginx",
        "Serverless", "Lambda", "EC2", "S3", "CloudFormation", "Ansible", "Vercel",
        "Netlify", "Render", "Heroku", "Cloudflare",
    ],
    "Testing & quality": [
        "Pytest", "Jest", "Mocha", "Cypress", "Playwright", "Selenium", "JUnit",
        "Unit Testing", "Integration Testing", "TDD", "Test Automation", "Postman",
    ],
    "Tools & practices": [
        "Git", "GitHub", "GitLab", "Bitbucket", "Jira", "Confluence", "Agile",
        "Scrum", "Kanban", "Figma", "Swagger", "OpenAPI", "VS Code", "Jupyter",
        "Code Review", "System Design", "Data Structures", "Algorithms", "OOP",
        "Design Patterns", "Debugging",
    ],
    "Soft skills": [
        "Communication", "Teamwork", "Leadership", "Problem Solving",
        "Time Management", "Collaboration", "Adaptability", "Critical Thinking",
        "Attention to Detail", "Ownership", "Mentoring",
    ],
}

# Aliases that should count as the canonical skill (lowercased matching).
ALIASES = {
    "nodejs": "Node.js", "node": "Node.js", "node js": "Node.js",
    "reactjs": "React", "react.js": "React", "react js": "React",
    "vuejs": "Vue", "vue.js": "Vue", "nextjs": "Next.js", "next js": "Next.js",
    "expressjs": "Express", "express.js": "Express", "express js": "Express",
    "javascript (js)": "JavaScript", "js": "JavaScript", "ts": "TypeScript",
    "py": "Python", "python3": "Python",
    "sklearn": "scikit-learn", "scikit learn": "scikit-learn",
    "postgres": "PostgreSQL", "mongo": "MongoDB",
    "amazon web services": "AWS", "aws cloud": "AWS",
    "google cloud platform": "GCP", "gcloud": "GCP",
    "ms azure": "Azure", "microsoft azure": "Azure",
    "natural language processing (nlp)": "NLP",
    "machine learning (ml)": "Machine Learning", "ml": "Machine Learning",
    "artificial intelligence": "AI", "gen ai": "GenAI",
    "lang chain": "LangChain", "huggingface": "Hugging Face",
    "ci cd": "CI/CD", "cicd": "CI/CD",
    "tailwindcss": "Tailwind CSS", "tailwind": "Tailwind CSS",
    "rest": "REST API", "restful": "REST API", "rest api": "REST API",
    "rest apis": "REST API", "restful api": "REST API", "restful apis": "REST API",
    "github actions": "GitHub Actions", "gh actions": "GitHub Actions",
    "k8s": "Kubernetes", "react native": "React Native",
    "vue js": "Vue", "angular js": "Angular", "angularjs": "Angular",
    "jquery": "jQuery", "py torch": "PyTorch", "tensor flow": "TensorFlow",
    "powerbi": "Power BI", "ms excel": "Excel", "microsoft excel": "Excel",
    "data structures and algorithms": "Data Structures", "dsa": "Data Structures",
    "object oriented programming": "OOP", "oops": "OOP",
    "spacy": "spaCy", "full-stack": "Full Stack", "fullstack": "Full Stack",
    "full stack development": "Full Stack", "nltk": "NLTK",
}

_WORD = r"(?<![A-Za-z0-9+#.]){}(?![A-Za-z0-9+#])"


def _compile(phrase: str) -> re.Pattern:
    return re.compile(_WORD.format(re.escape(phrase)), re.IGNORECASE)


_PATTERNS: list[tuple[str, re.Pattern]] = []
for group, skills in SKILL_GROUPS.items():
    for skill in skills:
        _PATTERNS.append((skill, _compile(skill)))
for alias, canonical in ALIASES.items():
    _PATTERNS.append((canonical, _compile(alias)))

_GROUP_OF = {skill: group for group, skills in SKILL_GROUPS.items() for skill in skills}


def extract_skills(text: str) -> dict[str, list[str]]:
    """Return {group: [skills]} found in the text, deduped, in lexicon order."""
    found: dict[str, list[str]] = {}
    for canonical, pattern in _PATTERNS:
        if pattern.search(text):
            group = _GROUP_OF.get(canonical, "Other")
            bucket = found.setdefault(group, [])
            if canonical not in bucket:
                bucket.append(canonical)
    return found


def flat_skills(grouped: dict[str, list[str]]) -> list[str]:
    return [s for skills in grouped.values() for s in skills]
