from __future__ import annotations

import shlex
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Literal, Optional, Set, Union

import rtoml
import yaml
from identify import identify
from pydantic import BaseModel, Field

from brood.constants import PACKAGE_NAME
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


class OnceConfig(BaseConfig):
    type: Literal["once"] = "once"


class RestartConfig(BaseConfig):
    type: Literal["restart"] = "restart"

    delay: float = Field(
        default=2, description="The delay before restarting the command after it exits.", ge=0
    )


class WatchConfig(BaseConfig):
    type: Literal["watch"] = "watch"

    paths: List[str] = Field(
        default_factory=list, description="The paths to watch (recursively) for changes."
    )
    poll: bool = Field(
        default=False,
        description="If true, poll for changes instead of waiting for change notifications.",
    )

    allow_multiple: bool = Field(
        default=False,
        description="If true, multiple instances of this command are allowed to run at once. If false, previous instances will be killed before starting a new one.",
    )


class CommandConfig(BaseConfig):
    name: str = Field(
        description="The name of this command. It is available as 'name' in the prefix format string."
    )

    command: Union[str, List[str]] = Field(
        description="The command to run, e.g. 'echo hello world'"
    )
    shutdown: Optional[Union[str, List[str]]] = Field(
        default=None,
        description=f"A command to run when {PACKAGE_NAME} is shutting down. Can be used to clean up from the 'command'.",
    )

    prefix: Optional[str] = Field(
        default=None,
        description=f"The format string for the prefix to display before each line of output from this command. Defaults to the renderer's 'prefix'.",
    )
    prefix_style: Optional[str] = Field(
        default=None,
        description=f"The Rich style to apply to the prefix. Defaults to the renderer's 'prefix_style'.",
    )
    message_style: Optional[str] = Field(
        default=None,
        description=f"The Rich style to apply to each line of output from this command. Defaults to the renderer's 'message_style'.",
    )

    starter: Union[OnceConfig, RestartConfig, WatchConfig] = RestartConfig()

    @property
    def command_string(self) -> str:
        if isinstance(self.command, list):
            return shlex.join(self.command)
        else:
            return self.command

    @property
    def shutdown_config(self) -> Optional[CommandConfig]:
        if self.shutdown is None:
            return None

        return self.copy(update={"command": self.shutdown, "starter": OnceConfig()})


class RendererConfig(BaseConfig):
    pass


class NullRendererConfig(RendererConfig):
    type: Literal["null"] = "null"


class LogRendererConfig(RendererConfig):
    type: Literal["log"] = "log"

    prefix: str = "{timestamp:%H:%M:%S.%f} {name} "

    prefix_style: str = Field(
        default="", description="The default style for prefixing command output."
    )

    internal_prefix: str = Field(
        default="{timestamp:%H:%M:%S.%f} ",
        description="The format string for the prefix to display before each internal message.",
    )
    internal_prefix_style: str = Field(
        default="dim",
        description="The style to apply to the prefix displayed before each internal message.",
    )
    internal_message_style: str = Field(
        default="dim", description="The style to apply to each internal message."
    )

    status_tracker: bool = Field(
        default=True,
        description="Enable/disable the process status tracker at the bottom of the terminal.",
    )


class FailureMode(str, Enum):
    CONTINUE = "continue"
    KILL_OTHERS = "kill_others"

    def __repr__(self) -> str:
        return repr(self.value)


ConfigFormat = Literal["json", "toml", "yaml"]


class BroodConfig(BaseConfig):
    failure_mode: FailureMode = Field(
        default=FailureMode.CONTINUE,
        description=f"How to react when a command fails. In {FailureMode.CONTINUE!r}, {PACKAGE_NAME} will continue running if a command fails. In {FailureMode.KILL_OTHERS!r}, {PACKAGE_NAME} will kill all other commands and exit if a command fails.",
    )

    commands: List[CommandConfig] = Field(default_factory=list, description="The commands to run.")
    renderer: Union[NullRendererConfig, LogRendererConfig] = Field(
        default=LogRendererConfig(), description="The renderer to use."
    )

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
