import argparse
import os
import subprocess
import sys
import time

from investigator.runtime.locking import investigation_lock

from .launch import (
    finish_worker,
    stop_on_disconnect,
    worker_command,
    worker_environment,
)
from .sessions import (
    InvestigationSession,
    SessionUnavailable,
)


def run_chat(
    session: InvestigationSession,
    seconds: float,
    query: str | None = None,
    stream_json: bool = False,
) -> int:
    process = subprocess.Popen(
        worker_command(session, seconds, query, stream_json),
        env=worker_environment(session),
    )

    try:
        end = time.monotonic() + seconds

        while process.poll() is None:
            session.check()

            if time.monotonic() >= end:
                raise SessionUnavailable("Investigation access expired")

            time.sleep(1)

        return process.returncode
    finally:
        finish_worker(process, session)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--profile", choices=["lab"], default="lab")
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--lookback-hours", type=int, default=2)
    parser.add_argument("--query")
    parser.add_argument("--stream-json", action="store_true")

    args = parser.parse_args()

    if os.geteuid() != 0:
        parser.error("Use sudo labctl chat")
    if not 1 <= args.minutes <= 30 or not 1 <= args.lookback_hours <= 24:
        parser.error("Use 1-30 minutes and 1-24 lookback hours")
    if args.query is None and not sys.stdin.isatty():
        parser.error("Interactive chat needs a terminal; use SSH with -t")

    specification = {
        "profile": args.profile,
        "ttl_seconds": args.minutes * 60,
        "lookback_seconds": args.lookback_hours * 3600,
    }

    try:
        with stop_on_disconnect(), investigation_lock() as acquired:
            if not acquired:
                raise SessionUnavailable(
                    "Another investigation is active; try again shortly"
                )

            with InvestigationSession(specification) as session:
                result = run_chat(
                    session, args.minutes * 60, args.query, args.stream_json
                )
    except SessionUnavailable as error:
        raise SystemExit(str(error)) from None
    except KeyboardInterrupt:
        raise SystemExit("Chat interrupted") from None

    raise SystemExit(result)


if __name__ == "__main__":
    main()
