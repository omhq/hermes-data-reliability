import time
import subprocess


def main() -> None:
    for _ in range(90):
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
                "hermes-lab-runtime",
            ],
            capture_output=True,
            text=True,
        )

        if result.stdout.strip() == "healthy":
            raise SystemExit(0)

        time.sleep(1)

    raise SystemExit("Hermes tool connection did not become healthy within 90 seconds")


if __name__ == "__main__":
    main()
