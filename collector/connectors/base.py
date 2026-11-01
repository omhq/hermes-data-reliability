from abc import ABC, abstractmethod
from typing import Any, ClassVar

from collector.execution import CollectionError, Deadline
from observations.models import Page, Source


class Connector(ABC):
    name: ClassVar[str]

    @abstractmethod
    def __init__(self, configuration: dict[str, Any]) -> None: ...

    @classmethod
    @abstractmethod
    def validate_definition(cls, value: object) -> dict[str, Any]: ...

    @classmethod
    def prepare_template(
        cls, value: dict[str, Any], configuration: dict[str, Any]
    ) -> dict[str, Any]:
        return value.copy()

    @abstractmethod
    def resolve(self, definition: dict[str, Any]) -> Source: ...

    @abstractmethod
    def authorize(self, source: Source) -> None: ...

    def read(
        self,
        source: Source,
        start: int,
        end: int,
        token: str | None,
        deadline: Deadline,
    ) -> Page:
        """Read a bounded page only after revalidating its source scope."""
        deadline.remaining()
        self.authorize(source)

        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end
            or token is not None
            and (type(token) is not str or not 1 <= len(token) <= 4096)
        ):
            raise CollectionError("invalid_collection_request")

        page = self._read(source, start, end, token, deadline)
        deadline.remaining()
        return page

    @abstractmethod
    def _read(
        self,
        source: Source,
        start: int,
        end: int,
        token: str | None,
        deadline: Deadline,
    ) -> Page: ...
