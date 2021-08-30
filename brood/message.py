from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Message:
    text: str
    timestamp: datetime = field(default_factory=datetime.now)
