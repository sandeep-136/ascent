from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=200, max_length=400_000)
    filename: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    doc_id: str | None = None  # None = search across the visitor's whole library
    visitor_llm_base_url: str | None = None
    visitor_llm_api_key: str | None = None
    visitor_llm_model: str | None = None


class GenerateRequest(BaseModel):
    kind: str = Field(pattern="^(summary|flashcards|quiz)$")
    style: str = "brief"
    visitor_llm_base_url: str | None = None
    visitor_llm_api_key: str | None = None
    visitor_llm_model: str | None = None


class LibraryRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=200)
