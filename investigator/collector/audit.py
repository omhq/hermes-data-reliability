import json
import os
import threading
from pathlib import Path
from typing import Any


class CollectionAudit:
    def __init__(
        self,
        path: str | Path,
        maximum: int = 1024 * 1024,
        copies: int = 3,
    ) -> None:
        self.path = Path(path)
        self.maximum = maximum
        self.copies = copies
        self.lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        payload = (
            json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")

        with self.lock:
            if (
                self.path.exists()
                and self.path.stat().st_size + len(payload) > self.maximum
            ):
                for index in range(self.copies, 0, -1):
                    source = (
                        self.path
                        if index == 1
                        else Path(str(self.path) + f".{index - 1}")
                    )

                    if source.exists():
                        source.replace(str(self.path) + f".{index}")

            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
            descriptor = os.open(self.path, flags, 0o600)

            with os.fdopen(descriptor, "ab") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
