# Password SSH Remote Design

## Goal

Make B300 remote use behave like ordinary OpenSSH/MobaXterm: remember the
Gateway endpoint (host, user, port), request the account password for each new
SSH connection, and never require a B300 public-key installation.

## User-approved behaviour

- The same password-interactive transport applies to `gateway connect-check`,
  Live Monitor, the integrated remote debug tunnel, and exported VS Code kits.
- The persistent profile contains only `host`, `user`, and `port`.  It must not
  contain a password, private-key path, public key, or host-key material.
- Password input is native SSH interaction, never a CLI argument, environment
  variable, JSON field, log value, or generated file.
- On the first connection OpenSSH performs its ordinary host-key prompt and
  then uses its normal user `known_hosts` memory.  Subsequent connections reuse
  that standard host record.  The application does not scan/enrol a separate
  B300 host-key store.
- OpenOCD GDB and Safe TCL stay bound to Gateway loopback.  Remote access is
  only SSH local forwarding; no 3333/6666 LAN listener is introduced.

## Architecture

Replace the managed-key policy as the default remote transport with a shared
password-interactive command builder.  It accepts a validated endpoint and
emits normal `ssh` argv: no `-i`, no managed `known_hosts`, and no password
value.  It explicitly prefers password/keyboard-interactive authentication and
disables public-key authentication so a stale agent/key cannot make behaviour
vary between machines.  It otherwise lets OpenSSH use the operator's normal
configuration and `known_hosts` handling.

The command builder is consumed by connectivity checks, Live Monitor, the GDB
and TCL tunnel configurations, and `RemoteVsCodeProfile`.  CLI children inherit
the invoking terminal so OpenSSH can request the password.  Windows GUI tunnel
launches create a visible console for the native prompt; generated VS Code
scripts are likewise interactive.  A password is requested once per created
SSH process/session, not stored for reuse.

The endpoint profile schema remains v1 because it already contains exactly the
allowed fields.  Existing saved profiles therefore work without migration.
Key generation, trust-host and authorize-key maintenance commands may remain as
legacy/manual utilities, but no default Client workflow may require or call
them.

## Error handling

- Missing OpenSSH reports the existing local prerequisite error.
- SSH authentication, cancellation, first-contact host-key refusal, and tunnel
  startup errors are returned as connection failures without echoing a
  password.
- A noninteractive caller with no TTY fails normally rather than accepting a
  password flag or falling back to a saved secret.
- All existing OpenOCD/flash safety interlocks are unchanged.

## Test strategy

- Unit-test a shared password SSH argv contains destination/port/forwarding and
  password-interactive options, but no `-i`, managed-key, known-hosts override,
  or password value.
- Update connectivity, Live Monitor, debug tunnel, VS Code and GUI tests to
  prove a saved endpoint works without managed key/trust files.
- Verify generated Windows and POSIX VS Code tunnel commands remain correctly
  quoted and interactive.
- Verify profile JSON remains endpoint-only and that output/log JSON never
  contains a password field.
- Run the affected modules in isolated processes, then the full 83-module
  Windows suite, compile checks, workflow YAML parse and release packaging
  smoke tests before any release decision.

## Non-goals

- Do not store, export, or autofill passwords.
- Do not expose GDB/TCL directly on the LAN or Internet.
- Do not change firmware flashing, Bootloader protection, Option Bytes, or
  debug operations on the physical board.
