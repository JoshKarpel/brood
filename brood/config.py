from pathlib import Path
from typing import Dict, List

import yaml
from pydantic import BaseModel, Field, validator

PROPAGATE_DEFAULT_FIELDS = {"prefix", "prefix_style", "message_style"}


class Command(BaseModel):
    command: str

    tag: str = Field(default="")
    prefix: str = Field(default="")
    prefix_style: str = Field(default="")
    message_style: str = Field(default="")

    restart_on_exit: bool = True
    restart_delay: int = 5


class Config(BaseModel):
    prefix: str = "{timestamp} {tag} "
    prefix_style: str = ""
    message_style: str = ""
    internal_prefix: str = "{timestamp} "
    internal_prefix_style: str = "dim"
    internal_message_style: str = "dim"
    verbose: bool = False
    commands: List[Command] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> "Config":
        toml = yaml.safe_load(Path(path).read_text())
        return Config.parse_obj(toml)

    @validator("commands", each_item=True)
    def propagate_defaults(cls, command: Command, values: Dict[str, object]) -> Command:
        for field in PROPAGATE_DEFAULT_FIELDS:
            setattr(command, field, getattr(command, field) or values[field])
        return command
