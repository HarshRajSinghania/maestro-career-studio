"""`python -m maestro_cs.prefetch`: download the scoring model once, with the backend's environment.

`maestro start` runs this in a child process whose environment is the backend's (cache folder,
home), so the model lands where the backend will look for it. It builds the exact object the
scorer builds, so there is no second download path to keep in step.
"""
from __future__ import annotations


def main() -> int:
    from app.services.ats import embeddings  # only after the environment is applied (R8)

    embeddings._model(embeddings.load_config().weights["semantic"]["model"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
