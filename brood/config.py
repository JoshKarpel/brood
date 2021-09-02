from __future__ import annotations

import shlex
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Literal, Optional, Set, Union

import rtoml
import yaml
from identify import identify
from pydantic import BaseModel, Field, PositiveFloat

from brood.errors import UnknownFormat

JSONDict = Dict[str, Any]


class BaseConfig(BaseModel):
    class Config:
        frozen = True
        use_enum_values = True

    def __hash__(self) -> int:
        return hash(self.__class__) + hash(
            tuple(v if not isinstance(v, list) else hash(tuple(v)) for v in self.__dict__.values())
        )


class StarterConfig(BaseConfig):
    delay: PositiveFloat = 2


class RestartConfig(StarterConfig):
    type: Literal["restart"] = "restart"

    restart_on_exit: bool = True


class WatchConfig(StarterConfig):
    type: Literal["watch"] = "watch"

    paths: List[str] = Field(default_factory=list)
    poll: bool = False

    allow_multiple: bool = False


class OnceConfig(StarterConfig):
    type: Literal["once"] = "once"


class CommandConfig(BaseConfig):
    command: Union[str, List[str]]
    shutdown: Optional[Union[str, List[str]]]

    tag: str = ""

    prefix: Optional[str] = None
    prefix_style: Optional[str] = None
    message_style: Optional[str] = None

    starter: Union[RestartConfig, WatchConfig, OnceConfig] = RestartConfig()

    @property
    def command_string(self) -> str:
        return normalize_command(self.command)

    @property
    def shutdown_string(self) -> Optional[str]:
        if self.shutdown is None:
            return None

        return normalize_command(self.shutdown)


def normalize_command(command: Union[str, List[str]]) -> str:
    if isinstance(command, list):
        return shlex.join(command)
    else:
        return command


class RendererConfig(BaseConfig):
    pass


class NullRendererConfig(RendererConfig):
    type: Literal["null"] = "null"


class LogRendererConfig(RendererConfig):
    type: Literal["log"] = "log"

    prefix: str = "{timestamp} {tag} "

    prefix_style: str = ""
    message_style: str = ""

    internal_prefix: str = "{timestamp} "
    internal_prefix_style: str = "dim"
    internal_message_style: str = "dim"


class FailureMode(str, Enum):
    CONTINUE = "continue"
    KILL_OTHERS = "kill_others"


ConfigFormat = Literal["json", "toml", "yaml"]


class BroodConfig(BaseConfig):
    failure_mode: FailureMode = FailureMode.CONTINUE

    verbose: bool = False

    commands: List[CommandConfig] = Field(default_factory=list)
    renderer: Union[NullRendererConfig, LogRendererConfig] = LogRendererConfig()

    FORMATS: ClassVar[Set[ConfigFormat]] = {"json", "toml", "yaml"}

    class Config:
        use_enum_values = True

    @classmethod
    def load(cls, path: Path) -> BroodConfig:
        tags = identify.tags_from_path(path)
        intersection = tags & cls.FORMATS

        if not intersection:
            raise UnknownFormat(f"Could not load config from {path}: unknown format.")

        text = path.read_text()
        for fmt in intersection:
            return getattr(cls, f"from_{fmt}")(text)
        else:  # pragma: unreachable
            raise UnknownFormat(f"No valid converter for {path}.")

    def save(self, path: Path) -> None:
        tags = identify.tags_from_filename(path)
        intersection = tags & self.FORMATS

        if not intersection:
            raise UnknownFormat(f"Could not write config to {path}: unknown format.")

        for fmt in intersection:
            path.write_text(self.to_format(fmt))
            return None
        else:  # pragma: unreachable
            raise UnknownFormat(f"No valid converter for {path}.")

    @classmethod
    def from_format(cls, t: str, format: ConfigFormat) -> BroodConfig:
        return getattr(cls, f"from_{format}")(t)

    def to_format(self, format: ConfigFormat) -> str:
        return getattr(self, format)()

    @classmethod
    def from_json(cls, j: str) -> BroodConfig:
        return BroodConfig.parse_raw(j)

    @classmethod
    def from_toml(cls, t: str) -> BroodConfig:
        return BroodConfig.parse_obj(rtoml.loads(t))

    def toml(self) -> str:
        return rtoml.dumps(self.dict())

    @classmethod
    def from_yaml(cls, y: str) -> BroodConfig:
        return BroodConfig.parse_obj(yaml.safe_load(y))

    def yaml(self) -> str:
        return yaml.dump(self.dict())
