"""Target boot-state inspection API."""

from .openocd import build_boot_verify_command, parse_boot_verification

__all__ = ["build_boot_verify_command", "parse_boot_verification"]
