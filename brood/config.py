from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Literal, Set, Union

import rtoml
import yaml
from identify import identify
from pydantic import BaseModel, Field

from brood.command import CommandConfig
from brood.renderer import LogRendererConfig, NullRendererConfig

JSONDict = Dict[str, Any]


class FailureMode(str, Enum):
    CONTINUE = "continue"
    KILL_OTHERS = "kill_others"


ConfigFormat = Literal["json", "toml", "yaml"]


class BroodConfig(BaseModel):
    failure_mode: FailureMode = FailureMode.CONTINUE

    verbose: bool = False

    commands: List[CommandConfig] = Field(default_factory=list)
    renderer: Union[NullRendererConfig, LogRendererConfig] = LogRendererConfig()

    FORMATS: ClassVar[Set[ConfigFormat]] = {"json", "toml", "yaml"}

    class Config:
        use_enum_values = True

    @classmethod
    def from_file(cls, path: Path) -> BroodConfig:
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
    def from_fmt(cls, t: str, format: ConfigFormat) -> BroodConfig:
        return getattr(cls, f"from_{format}")(t)

    def to_fmt(self, format: ConfigFormat) -> str:
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
