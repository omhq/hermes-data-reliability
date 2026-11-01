from pathlib import Path
from typing import Any

from observations.configurations.base import Configuration, ConfigurationGraph
from observations.subscriptions import Consumer, manifest

from .definition import load
from .matching import Matcher


class LogRulesConfiguration(Configuration):
    @classmethod
    def load(
        cls,
        source: Path,
        approved: dict[str, Any],
        *,
        resolve_template: bool = False,
    ) -> LogRulesConfiguration:
        return cls(load(source))

    def consumers(self) -> tuple[Consumer, ...]:
        return (manifest(self.definition),)

    def validate_graph(self, graph: ConfigurationGraph) -> None:
        for rule in self.definition["rules"].values():
            if graph.sources[rule["source"]].connector != "cloudwatch_logs":
                raise ValueError("Log rules require CloudWatch log sources")

        with Matcher(self.definition["rules"]):
            pass
