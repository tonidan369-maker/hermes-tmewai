"""Root shim: the plugin package lives in ``tmewai/`` (catalog subdir).

``hermes plugins install <owner>/<repo>`` clones the repository root and expects
``plugin.yaml`` + ``__init__.py`` there, so the root re-exports the real package
instead of duplicating 3k lines of code.
"""

# Relative, not absolute: an installed plugin is imported under the name
# "tmewai", so `from tmewai import ...` would re-enter this very file.
from .tmewai import register  # noqa: F401

__all__ = ["register"]
