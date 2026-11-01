"""Minimal Windows port of koe-kichi.

This package intentionally avoids importing the macOS `voice_typer` package.
The macOS package depends on PyObjC and MLX, neither of which is appropriate
for a small Windows build.
"""

__all__ = ["__version__"]

__version__ = "0.1.3"
