from typing import Literal

from pydantic import BaseModel, Field, model_validator

Department = Literal["hr", "management", "technical"]


class Task(BaseModel):
    department: Department
    question: str = Field(min_length=1, max_length=8000)


class Route(BaseModel):
    tasks: list[Task] = Field(default_factory=list, max_length=3)
    clarification: str = ""

    @model_validator(mode="after")
    def valid_route(self):
        if len({t.department for t in self.tasks}) != len(self.tasks):
            raise ValueError("Duplicate department")
        if bool(self.tasks) == bool(self.clarification.strip()):
            raise ValueError("Provide tasks OR a clarification")
        return self


class Queries(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=3)


class Claim(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    source_ids: list[str] = Field(min_length=1, max_length=6)


class Answer(BaseModel):
    status: Literal["answered", "insufficient_evidence", "conflicting_evidence"]
    claims: list[Claim] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def check_answer(self):
        if self.status != "insufficient_evidence" and not self.claims:
            raise ValueError("Answered responses require sourced claims")
        if self.status == "insufficient_evidence" and self.claims:
            raise ValueError("Insufficient-evidence response cannot include policy claims")
        return self


class Passage(BaseModel):
    id: str
    text: str
    uri: str
    title: str
    corpus: str
    first_page: int | None = None
    last_page: int | None = None
