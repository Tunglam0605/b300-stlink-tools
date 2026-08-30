"""Shared CLI output-path safety helpers."""

from __future__ import annotations

from pathlib import Path


def validated_output_path(path: Path, force: bool) -> Path:
    output = Path(path).expanduser().resolve()
    if not output.parent.is_dir() or output.is_dir():
        raise ValueError("Output path must name a file in an existing directory.")
    if output.exists() and not force:
        raise FileExistsError("Output file already exists; use --force to replace it.")
    return output
