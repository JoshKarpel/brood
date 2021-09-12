from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, unique
from functools import total_ordering
from typing import Union

from brood.config import CommandConfig


@unique
@total_ordering
class Verbosity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def is_debug(self) -> bool:
        return self <= self.DEBUG

    def __int__(self) -> int:
        if self is self.DEBUG:
            return 0
        elif self is self.INFO:
            return 1
        elif self is self.WARNING:
            return 2
        elif self is self.ERROR:
            return 3
        else:  # pragma: unreachable
            raise Exception("unreachable")

    def __lt__(self, other: object) -> bool:
        if isinstance(other, Verbosity):
            return int(self) < int(other)
        return NotImplemented


@dataclass(frozen=True)
class InternalMessage:
    text: str
    verbosity: Verbosity
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class CommandMessage:
    text: str
    command_config: CommandConfig
    timestamp: datetime = field(default_factory=datetime.now)


Message = Union[InternalMessage, CommandMessage]
