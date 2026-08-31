"""Single source of truth for the B300 ST-Link Tools release version."""

import argparse

__version__ = "0.14.0"
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", metavar="VERSION")
    parser.add_argument("--print", action="store_true", dest="print_version")
    args = parser.parse_args(argv)
    if args.check is not None and args.check != __version__:
        parser.error(
            "release version %s does not match source version %s" %
            (args.check, __version__)
        )
    if args.print_version or args.check is None:
        print(__version__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
