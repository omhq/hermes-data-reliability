from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from observations.models import Source
from observations.subscriptions import Consumer


@dataclass(frozen=True)
class ConfigurationGraph:
    approved: dict[str, Any]
    sources: Mapping[str, Source]
    collection: dict[str, Any]


class Configuration(ABC):
    def __init__(self, definition: dict[str, Any]) -> None:
        self.definition = definition

    @classmethod
    @abstractmethod
    def load(
        cls,
        source: Path,
        approved: dict[str, Any],
        *,
        resolve_template: bool = False,
    ) -> Configuration: ...

    def sources(self, approved: dict[str, Any]) -> dict[str, Source]:
        return {}

    def consumers(self) -> tuple[Consumer, ...]:
        return ()

    def validate_graph(self, graph: ConfigurationGraph) -> None:
        """Validate package constraints before installing configuration."""

    def compile(self, graph: ConfigurationGraph) -> dict[str, Any]:
        return self.definition
