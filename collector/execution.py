import time


class CollectionError(RuntimeError):
    def __init__(self, code: str, *, resume: bool = False) -> None:
        self.code = code
        self.resume = resume
        super().__init__(code)


class Deadline:
    def __init__(self, seconds: float) -> None:
        self.end = time.monotonic() + seconds

    def remaining(self) -> float:
        remaining = self.end - time.monotonic()

        if remaining <= 0:
            raise CollectionError("execution_timeout")

        return remaining
