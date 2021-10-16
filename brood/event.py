from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brood.command import Command


@unique
class EventType(Enum):
    Started = "started"
    Stopped = "stopped"


@dataclass(frozen=True)
class Event:
    command: Command
    type: EventType
