from tool_boundary.server import main as serve

from .extension import load


def main() -> None:
    serve(extension_factory=load)


if __name__ == "__main__":
    main()
