"""Make the optional extras unimportable, whatever is installed.

A12 is the claim that the spine runs on the standard library alone. Checking it
by asserting that Chroma and friends are absent from the environment tests the
wrong thing: it passes or fails on what the runner image happens to ship, and it
says nothing about whether the code actually needs them.

This instead blocks the imports and runs the suite anyway. Put this directory on
PYTHONPATH and every optional dependency raises ImportError on import, so the
degradation paths in src/retrieve.py and src/monitoring.py are the ones under
test — on a machine where the extras are installed just as much as on one where
they are not.

    PYTHONPATH=scripts/ci/no_extras python -m unittest discover -s tests
"""

import sys

BLOCKED = frozenset({
    "chromadb", "sentence_transformers", "torch", "transformers",
    "prometheus_client", "fastapi", "uvicorn", "dotenv", "pytest",
})


class _BlockExtras:
    """A meta path finder that refuses the optional dependencies."""

    def find_spec(self, fullname, path=None, target=None):
        root = fullname.partition(".")[0]
        if root in BLOCKED:
            raise ImportError(
                f"{root!r} is blocked: the spine must run without the optional "
                f"extras (A12). See scripts/ci/no_extras/sitecustomize.py."
            )
        return None


if not any(isinstance(f, _BlockExtras) for f in sys.meta_path):
    sys.meta_path.insert(0, _BlockExtras())
