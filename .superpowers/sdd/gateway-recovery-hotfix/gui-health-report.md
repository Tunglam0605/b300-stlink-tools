# GUI health recovery hotfix

## Change

When legacy `gateway-status` reports `STOPPED`, the shared context bar now
renders authenticated Gateway Agent/lease evidence. Inactive leases show
`IDLE`, active leases show `BUSY`, and recovery leases show
`RECOVERY_REQUIRED`, each with the authenticated reason code and active client
label where available.

Authenticated evidence is cleared on any transport failure, preventing stale
ownership from being displayed while the controller is losing contact. The
legacy snapshot remains authoritative for `attach_ready`; this change does not
make a stopped Gateway attachable.

## Validation

`python -m unittest tests.test_gateway_health_ui tests.test_gateway_health_controller -q`

Result: 17 tests passed.

## Scope

Changed only GUI/controller code, focused GUI/controller tests, and this report.
No coordinator or supervisor files were modified.
