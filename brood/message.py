from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Union

from brood.config import CommandConfig


@dataclass(frozen=True)
class InternalMessage:
    text: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class CommandMessage:
    text: str
    command_config: CommandConfig
    timestamp: datetime = field(default_factory=datetime.now)


Message = Union[InternalMessage, CommandMessage]
