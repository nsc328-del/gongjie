from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    world_id: str
    character_id: str


class TurnInput(BaseModel):
    content: str = ""
    continuation: bool = False


class ProviderInput(BaseModel):
    id: str = "provider_default"
    name: str = "我的模型"
    protocol: str
    base_url: str
    path: str
    model: str
    api_key: str = ""
    max_tokens: int = Field(default=16384, ge=256, le=200000)
    temperature: float = Field(default=0.95, ge=0, le=2)


class WorldInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    premise: str = ""
    atmosphere: str = ""
    rules: str = ""
    opening_narration: str = ""
    current_time: str = ""
    template_id: str | None = None


class LocationInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = ""
    mood: str = ""


class CharacterInput(BaseModel):
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


class RoomInput(BaseModel):
    name: str = Field(default="同场", max_length=60)
    location_id: str | None = None
    character_ids: list[str] = Field(default_factory=list)
