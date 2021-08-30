from pathlib import Path
from typing import List, Optional

import rtoml
from pydantic import BaseModel, Field


class Command(BaseModel):
    command: str
    tag: str = Field(default="")
    prefix_style: Optional[str]
    line_style: Optional[str]


class Config(BaseModel):
    prefix: str
    commands: List[Command] = Field(default=list)

    @classmethod
    def from_toml(cls, path: Path) -> "Config":
        toml = rtoml.loads(Path(path).read_text())
        return Config.parse_obj(toml)
