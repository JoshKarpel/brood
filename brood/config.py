import json
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Literal, Optional, Set

import rtoml
import yaml
from identify import identify
from pydantic import BaseModel, Field, validator

PROPAGATE_DEFAULT_FIELDS = {"prefix", "prefix_style", "message_style"}
JSONDict = Dict[str, Any]


class FailureMode(str, Enum):
    CONTINUE = "continue"
    KILL_OTHERS = "kill_others"


class Command(BaseModel):
    command: str

    tag: str = Field(default="")
    prefix: str = Field(default="")
    prefix_style: str = Field(default="")
    message_style: str = Field(default="")

    restart_on_exit: bool = True
    restart_delay: int = 5


ConfigFormat = Literal["json", "toml", "yaml"]


class Config(BaseModel):
    prefix: str = "{timestamp} {tag} "

    prefix_style: str = ""
    message_style: str = ""

    internal_prefix: str = "{timestamp} "
    internal_prefix_style: str = "dim"
    internal_message_style: str = "dim"

    failure_mode: FailureMode = FailureMode.KILL_OTHERS

    verbose: bool = False

    commands: List[Command] = Field(default_factory=list)

    FORMATS: ClassVar[Set[ConfigFormat]] = {"json", "toml", "yaml"}

    @validator("commands", each_item=True)
    def propagate_defaults(cls, command: Command, values: Dict[str, object]) -> Command:
        for field in PROPAGATE_DEFAULT_FIELDS:
            setattr(command, field, getattr(command, field) or values[field])
        return command

    @classmethod
    def from_file(cls, path: Path) -> "Config":
        tags = identify.tags_from_path(path)
        intersection = tags & cls.FORMATS

        if not intersection:
            raise ValueError(f"Could not load config from {path}: unknown format.")

        text = path.read_text()
        for fmt in intersection:
            return getattr(cls, f"from_{fmt}")(text)
        else:
            raise ValueError(f"No valid converter for {path}.")

    def to_file(self, path: Path) -> None:
        tags = identify.tags_from_filename(path)
        intersection = tags & self.FORMATS

        if not intersection:
            raise ValueError(f"Could not write config to {path}: unknown format.")

        for fmt in intersection:
            path.write_text(self.to_fmt(fmt))
            return None
        else:
            raise ValueError(f"No valid converter for {path}.")

    @classmethod
    def from_fmt(cls, t: str, format: ConfigFormat) -> "Config":
        return getattr(cls, f"from_{format}")(t)

    def to_fmt(self, format: ConfigFormat) -> str:
        return getattr(self, f"to_{format}")()

    def to_dict(self) -> JSONDict:
        return json.loads(self.json())

    @classmethod
    def from_json(cls, j: str) -> "Config":
        return Config.parse_raw(j)

    def to_json(self) -> str:
        return self.json()

    @classmethod
    def from_toml(cls, t: str) -> "Config":
        return Config.parse_obj(rtoml.loads(t))

    def to_toml(self) -> str:
        return rtoml.dumps(self.to_dict())

    @classmethod
    def from_yaml(cls, y: str) -> "Config":
        return Config.parse_obj(yaml.safe_load(y))

    def to_yaml(self) -> str:
        return yaml.dump(self.to_dict())
