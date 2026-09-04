"""Verify Client AXF/ELF symbols over the authenticated shared RemoteSession."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from .elf_matcher import discover_symbol_files, find_matching_symbol_file
from .remote_session import RemoteSession
from .tcl_client import SafeTclClient, TclEndpoint


def verify_client_symbols(
    remote_session: RemoteSession,
    *,
    symbol_file: Optional[Path] = None,
    symbol_roots: Sequence[Path] = (),
    gateway_tcl_port: int = 6666,
    max_files: int = 128,
    tcl_factory=SafeTclClient,
) -> Optional[Path]:
    """Return the unique AXF/ELF matching remote Application Flash.

    The function reuses the already-authenticated SSH transport and its loopback-only
    TCL forward. It never changes RUN/HALT state and never starts a second SSH process.
    ``None`` is returned only when the caller supplied no symbol file/root at all.
    """
    health = remote_session.check_health()
    if not health.authenticated:
        raise RuntimeError("Client symbol verification requires an authenticated RemoteSession.")

    exact = Path(symbol_file).expanduser().resolve() if symbol_file is not None else None
    if exact is not None:
        if exact.suffix.lower() not in {".elf", ".axf"} or not exact.is_file():
            raise ValueError("Client debug symbols must reference an existing ELF/AXF file.")

    roots = tuple(Path(root).expanduser().resolve() for root in symbol_roots)
    for root in roots:
        if not root.is_dir():
            raise ValueError("Client symbol root does not exist or is not a directory: %s" % root)

    if exact is None and not roots:
        return None

    forward = remote_session.open_forward(
        "tcl", remote_port=int(gateway_tcl_port), local_port=0,
    )
    tcl = tcl_factory(TclEndpoint(forward.local_host, forward.local_port))
    state_before = tcl.wait_target_state()

    candidates = (exact,) if exact is not None else discover_symbol_files(
        roots, max_files=max_files, max_depth=8,
    )
    if not candidates:
        raise RuntimeError("No AXF/ELF was found under the configured Client symbol roots.")

    selected, results = find_matching_symbol_file(candidates, tcl.read_words)
    state_after = tcl.wait_target_state()
    if state_after != state_before:
        raise RuntimeError(
            "Client symbol verification changed target state unexpectedly: %s -> %s" %
            (state_before, state_after)
        )

    if selected is not None:
        return selected.path

    exact_matches = sum(1 for item in results if item.matched)
    if exact_matches > 1:
        raise RuntimeError("Multiple AXF/ELF files match remote firmware; select one explicitly.")
    if exact is not None:
        detail = results[0].reason if results else "AXF/ELF could not be parsed or sampled"
        raise RuntimeError("Selected AXF/ELF does not match remote firmware: %s" % detail)
    raise RuntimeError("No AXF/ELF under the configured Client project matches remote firmware.")
