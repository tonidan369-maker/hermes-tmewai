"""Root shim: the plugin package lives in ``tmewai/`` (catalog subdir).

``hermes plugins install <owner>/<repo>`` clones the repository root and expects
``plugin.yaml`` + ``__init__.py`` there, so the root re-exports the real package
instead of duplicating 3k lines of code.
"""

from tmewai import register  # noqa: F401

__all__ = ["register"]
