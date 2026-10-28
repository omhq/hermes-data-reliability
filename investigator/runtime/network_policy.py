import subprocess


def main() -> None:
    rule = [
        "DOCKER-USER",
        "-i",
        "br-hl-runtime",
        "-d",
        "169.254.169.254/32",
        "-j",
        "REJECT",
    ]

    if subprocess.run(
        ["iptables", "-C", *rule],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        subprocess.run(
            ["iptables", "-I", "DOCKER-USER", "1", *rule[1:]], check=True
        )


if __name__ == "__main__":
    main()
