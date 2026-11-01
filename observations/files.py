import os
import tempfile
from pathlib import Path


def install_bytes(
    destination: Path, payload: bytes, mode: int = 0o644
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        dir=destination.parent, delete=False
    ) as stream:
        staged = Path(stream.name)

        try:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            staged.chmod(mode)
            staged.replace(destination)
        finally:
            staged.unlink(missing_ok=True)
