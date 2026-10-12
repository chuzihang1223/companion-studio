from pydantic import BaseModel


class CharacterPublic(BaseModel):
    id: str
    name: str
    english_name: str
    tagline: str
    welcome_message: str
    identity: str = ""
    avatar_url: str = ""
    card_format: str = "native_json"
