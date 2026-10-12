"""The `maestro` command: run, update and connect a uv-installed Maestro Career Studio."""
from importlib import metadata


def package_version() -> str:
    try:
        return metadata.version("maestro-career-studio")
    except metadata.PackageNotFoundError:  # a source tree that was never installed
        return "dev"
