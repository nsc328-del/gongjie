from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class ConversationCreate(InputModel):
    world_id: str
    character_id: str


class TurnInput(InputModel):
    content: str = ""
    continuation: bool = False


class ProviderInput(InputModel):
    id: str = "provider_default"
    name: str = "我的模型"
    protocol: Literal["openai-compatible", "anthropic-messages"]
    base_url: str
    path: str
    model: str
    api_key: str = ""
    max_tokens: int = Field(default=16384, ge=256, le=200000)
    temperature: float = Field(default=0.95, ge=0, le=2)


class WorldInput(InputModel):
    name: str = Field(min_length=1, max_length=60)
    premise: str = ""
    atmosphere: str = ""
    rules: str = ""
    opening_narration: str = ""
    current_time: str = ""
    template_id: str | None = None


class WorldResetInput(InputModel):
    confirmation: str


class PulseInput(InputModel):
    preview_id: str = Field(default="", max_length=80)


class LocationInput(InputModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = ""
    mood: str = ""


class CharacterInput(InputModel):
    name: str = Field(min_length=1, max_length=60)
    age: int = Field(ge=18, le=999)
    pronouns: str = ""
    role: str = ""
    tagline: str = ""
    opening_hook: str = ""
    persona: str = ""
    speech_style: str = ""
    desire_profile: dict[str, int] = Field(default_factory=dict)
    signature_behaviors: list[str] = Field(default_factory=list)
    loss_of_control: str = ""
    current_location_id: str | None = None
    accent: str = "#bc5d45"


class RoomInput(InputModel):
    name: str = Field(default="同场", min_length=1, max_length=60)
    location_id: str | None = None
    character_ids: list[str] = Field(default_factory=list)


class PlayerProfileInput(InputModel):
    name: str = Field(default="", max_length=60)
    age: int | None = Field(default=None, ge=18, le=999)
    pronouns: str = Field(default="", max_length=20)
    appearance: str = Field(default="", max_length=2000)
    identity: str = Field(default="", max_length=2000)
