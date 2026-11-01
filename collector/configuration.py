from pathlib import Path
from typing import Any

from observations.configurations.base import Configuration, ConfigurationGraph
from observations.models import Source

from .definition import load, resolve_sources
from .registered import CONNECTORS


class SourceCatalogConfiguration(Configuration):
    @classmethod
    def load(
        cls,
        source: Path,
        approved: dict[str, Any],
        *,
        resolve_template: bool = False,
    ) -> SourceCatalogConfiguration:
        return cls(load(source, approved, resolve_template=resolve_template))

    def sources(self, approved: dict[str, Any]) -> dict[str, Source]:
        return resolve_sources(self.definition, CONNECTORS.configure(approved))

    def compile(self, graph: ConfigurationGraph) -> dict[str, Any]:
        return graph.collection
