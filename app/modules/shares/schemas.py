from pydantic import BaseModel, Field

NAME_PATTERN = r"^[a-z][a-z0-9_-]{1,31}$"


class ShareCreate(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    path: str = ""
    comment: str = Field(default="", max_length=255)
    wide_links: bool = False


class ShareUpdate(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    path: str = ""
    comment: str = Field(default="", max_length=255)
    wide_links: bool = False


class ShareOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    name: str
    path: str
    comment: str | None
    wide_links: bool