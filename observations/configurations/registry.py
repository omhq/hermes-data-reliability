from dataclasses import dataclass
from importlib import import_module
from inspect import isabstract
from pathlib import Path
from typing import Any

from observations.configuration import InvalidConfiguration, name

from .base import Configuration


@dataclass(frozen=True)
class ConfigurationSpec:
    identifier: str
    yaml_filename: str
    compiled_filename: str
    provider: str
    template: Path | None = None

    def __post_init__(self) -> None:
        name(self.identifier)

        for filename in (self.yaml_filename, self.compiled_filename):
            if (
                not filename
                or filename.startswith(".")
                or Path(filename).name != filename
            ):
                raise InvalidConfiguration("Configuration needs a filename")

        if self.template is not None and (
            self.template.is_absolute() or ".." in self.template.parts
        ):
            raise InvalidConfiguration("Template must be inside its root")

        module, separator, attribute = self.provider.partition(":")

        if not module or not separator or not attribute.isidentifier():
            raise InvalidConfiguration("Invalid configuration provider")

    def template_path(self, root: Path) -> Path:
        return root / (self.template or self.yaml_filename)

    def load(
        self,
        source: Path,
        approved: dict[str, Any],
        *,
        resolve_template: bool = False,
    ) -> Configuration:
        """Load only the implementation named by trusted registration code."""
        module, _, attribute = self.provider.partition(":")
        provider = getattr(import_module(module), attribute, None)

        if (
            not isinstance(provider, type)
            or not issubclass(provider, Configuration)
            or isabstract(provider)
        ):
            raise InvalidConfiguration("Invalid configuration provider")

        configuration = provider.load(
            source, approved, resolve_template=resolve_template
        )

        if not isinstance(configuration, Configuration):
            raise InvalidConfiguration("Invalid configuration implementation")

        return configuration


class ConfigurationRegistry:
    def __init__(
        self, catalog: ConfigurationSpec, *consumers: ConfigurationSpec
    ) -> None:
        self.catalog = catalog
        self.entries = (catalog, *consumers)

        for values in (
            tuple(entry.identifier for entry in self.entries),
            self.yaml_filenames,
            self.compiled_filenames,
        ):
            if len(set(values)) != len(values):
                raise InvalidConfiguration(
                    "Duplicate configuration registration"
                )

        if set(self.yaml_filenames) & set(self.compiled_filenames):
            raise InvalidConfiguration("Configuration filenames overlap")

    @property
    def yaml_filenames(self) -> tuple[str, ...]:
        return tuple(entry.yaml_filename for entry in self.entries)

    @property
    def compiled_filenames(self) -> tuple[str, ...]:
        return tuple(entry.compiled_filename for entry in self.entries)
