from observations.models import Page, Source

from .audit import CollectionAudit
from .execution import Deadline
from .registry import ConfiguredConnectors


class ConnectorReader:
    def __init__(
        self, connectors: ConfiguredConnectors, audit: CollectionAudit
    ) -> None:
        self.connectors = connectors
        self.audit = audit
        self.deadline = Deadline(60)

    def read(
        self, source: Source, start: int, end: int, token: str | None
    ) -> Page:
        self.audit.write(
            {
                "status": "collection_read",
                "source": source.key,
                "start_ms": start,
                "end_ms": end,
            }
        )

        try:
            page = self.connectors.get(source.connector).read(
                source, start, end, token, self.deadline
            )
        except Exception:
            self.audit.write(
                {"status": "collection_failed", "source": source.key}
            )
            raise

        self.audit.write(
            {
                "status": "collection_completed",
                "source": source.key,
                "events": len(page.events),
                "partial": page.continuation is not None,
            }
        )
        return page
