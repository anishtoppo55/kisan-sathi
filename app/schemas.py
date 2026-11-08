from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.modules.yojana import FarmerProfile

MAX_MESSAGE_CHARS = 1000


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    session_id: str | None = Field(default=None, max_length=64)
    lang: str | None = Field(default=None, max_length=10)
    speak: bool = True  # also return TTS audio

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be blank")
        return v


class Card(BaseModel):
    type: str  # scheme | eligibility | crop | weather | web_sources
    data: dict[str, Any] = Field(default_factory=dict)


class Source(BaseModel):
    title: str
    url: str


class AssistantResponse(BaseModel):
    """Single response format shared by every endpoint."""

    transcript: str
    lang: str
    reply_text: str
    cards: list[Card] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    audio_b64: str | None = None


class EligibilityRequest(BaseModel):
    profile: FarmerProfile
    session_id: str | None = Field(default=None, max_length=64)
    lang: str | None = Field(default=None, max_length=10)
    speak: bool = False
