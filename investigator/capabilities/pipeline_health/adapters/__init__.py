from ..observations import EvidenceAdapter
from .collected import CollectedEventsAdapter
from .s3 import S3ObjectAdapter

ADAPTERS: dict[str, EvidenceAdapter] = {
    "s3_object": S3ObjectAdapter(),
    "collected_events": CollectedEventsAdapter(),
}
