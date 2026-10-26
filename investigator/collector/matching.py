import re
import sys
import multiprocessing

from multiprocessing.connection import Connection
from typing import Any, Literal, Self

if sys.platform == "linux":
    import resource

MAX_BLOCK_BYTES = 256 * 1024
MAX_BATCH_BYTES = 2 * 1024 * 1024
MAX_WORKER_MEMORY_BYTES = 256 * 1024 * 1024
WORKER_TIMEOUT = 3

type CompiledCondition = (
    tuple[Literal["contains"], str]
    | tuple[Literal["regex"], re.Pattern[str]]
    | tuple[Literal["all", "any"], list[CompiledCondition]]
)


class MatchError(RuntimeError):
    pass


def compile_condition(value: dict[str, Any]) -> CompiledCondition:
    operator, operand = next(iter(value.items()))

    if operator == "regex":
        return operator, re.compile(operand)
    if operator == "contains":
        return operator, operand

    if operator in ("all", "any"):
        return operator, [compile_condition(child) for child in operand]

    raise MatchError("Unsupported match operator")


def evaluate(compiled: CompiledCondition, text: str) -> list[list[int]]:
    if compiled[0] == "contains":
        operand = compiled[1]
        start = text.find(operand)
        return [] if start < 0 else [[start, start + len(operand)]]
    if compiled[0] == "regex":
        matched = compiled[1].search(text)
        return [] if matched is None else [list(matched.span())]

    operator, children = compiled
    spans = []

    for child in children:
        matched = evaluate(child, text)

        if operator == "all" and not matched:
            return []
        if operator == "any" and matched:
            return matched

        spans.extend(matched)

    return spans


def limit_worker_memory() -> None:
    """Cap Linux worker address space before compiling untrusted patterns."""
    if sys.platform != "linux":
        return

    resource.setrlimit(
        resource.RLIMIT_AS,
        (MAX_WORKER_MEMORY_BYTES, MAX_WORKER_MEMORY_BYTES),
    )


def worker(connection: Connection, rules: dict[str, dict[str, Any]]) -> None:
    limit_worker_memory()

    try:
        compiled = {
            name: compile_condition(rule["match"])
            for name, rule in rules.items()
        }
        connection.send({"ok": True})

        while True:
            texts = connection.recv()
            matches = []

            for text in texts:
                matches.append(
                    {
                        name: spans
                        for name, expression in compiled.items()
                        if (spans := evaluate(expression, text))
                    }
                )

            connection.send({"ok": True, "matches": matches})
    except EOFError, BrokenPipeError:
        pass
    except Exception:
        connection.send({"ok": False})
    finally:
        connection.close()


class Matcher:
    """A worker compiles once and serves successive provider pages."""

    def __init__(
        self, rules: dict[str, dict[str, Any]], timeout: float = WORKER_TIMEOUT
    ) -> None:
        self.rules = rules
        self.timeout = timeout
        self.process = None
        self.connection = None

    def __enter__(self) -> Self:
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=worker, args=(child, self.rules))

        self.process.start()
        child.close()

        try:
            self.receive()
        except Exception:
            self.__exit__()
            raise

        return self

    def receive(self) -> dict[str, Any]:
        connection = self.connection

        if connection is None or connection.closed:
            raise MatchError("Rule worker is not running")
        if not connection.poll(self.timeout):
            raise MatchError("Rule compilation or evaluation timed out")

        try:
            response = connection.recv()
        except EOFError, OSError:
            raise MatchError("Rule worker stopped") from None

        if not response.get("ok"):
            raise MatchError("Invalid or unevaluable match expression")

        return response

    def match(self, texts: list[str]) -> list[dict[str, list[list[int]]]]:
        sizes = [len(text.encode("utf-8")) for text in texts]

        if any(size > MAX_BLOCK_BYTES for size in sizes) or sum(sizes) > (
            MAX_BATCH_BYTES
        ):
            raise MatchError("Log blocks exceed the evaluation byte limit")

        connection = self.connection

        if connection is None or connection.closed:
            raise MatchError("Rule worker is not running")

        try:
            connection.send(texts)
            return self.receive()["matches"]
        except BrokenPipeError, EOFError, OSError:
            raise MatchError("Rule worker stopped") from None

    def __exit__(self, *_: object) -> None:
        if self.connection is not None:
            self.connection.close()

        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()

            self.process.join(timeout=1)

            if self.process.is_alive():
                self.process.kill()
                self.process.join()
