from __future__ import annotations

from asyncio import Queue
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import TracebackType
from typing import Callable, ContextManager, Optional, Type

import git
from gitignore_parser import parse_gitignore
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from brood.command import CommandConfig, WatchConfig


@dataclass
class FileWatcher(ContextManager):
    config: WatchConfig
    event_handler: FileSystemEventHandler

    def __post_init__(self) -> None:
        self.observer = (PollingObserver if self.config.poll else Observer)(timeout=0.1)

    def start(self) -> FileWatcher:
        for path in self.config.paths:
            self.observer.schedule(self.event_handler, str(path), recursive=True)
        self.observer.start()

        return self

    def stop(self) -> FileWatcher:
        self.observer.stop()
        return self

    def join(self) -> FileWatcher:
        self.observer.join()
        return self

    def __enter__(self) -> FileWatcher:
        return self.start()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        self.stop()
        self.join()
        return None


@dataclass(frozen=True)
class StartCommand(FileSystemEventHandler):
    command_config: CommandConfig
    event_queue: Queue = field(default_factory=Queue)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        try:
            if get_ignorer(get_git_root(Path(event.src_path)) / ".gitignore")(event.src_path):
                return
        except Exception:
            return

        self.event_queue.put_nowait((self.command_config, event))

    def __hash__(self) -> int:
        return hash((type(self), id(self)))


@cache
def get_git_root(path: Path) -> Path:
    git_repo = git.Repo(str(path), search_parent_directories=True)
    git_root = git_repo.git.rev_parse("--show-toplevel")
    return Path(git_root)


@cache
def get_ignorer(path: Path) -> Callable[[str], bool]:
    return parse_gitignore(str(path))
