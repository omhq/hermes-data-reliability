from pathlib import Path
from typing import Any

from observations.configurations.base import Configuration, ConfigurationGraph
from observations.subscriptions import Consumer
from observations.yaml import load_yaml

from .collection import with_collection
from .definition import Definition, bind, parse


class PipelineHealthConfiguration(Configuration):
    def __init__(self, definition: dict[str, Any], parsed: Definition) -> None:
        super().__init__(definition)
        self.parsed = parsed

    @classmethod
    def load(
        cls,
        source: Path,
        approved: dict[str, Any],
        *,
        resolve_template: bool = False,
    ) -> PipelineHealthConfiguration:
        definition = load_yaml(source)
        return cls(definition, parse(definition))

    def consumers(self) -> tuple[Consumer, ...]:
        return (self.parsed.consumer,)

    def validate_graph(self, graph: ConfigurationGraph) -> None:
        bind(
            self.parsed,
            with_collection(graph.approved, self.parsed, graph.collection),
        )

        if (
            self.parsed.consumer.enabled
            and self.parsed.pipelines
            and "hermes-pipeline-health"
            not in graph.approved.get("tool_packages", [])
        ):
            raise ValueError(
                "Enable the pipeline-health tool package "
                "before scheduling checks"
            )
