# Gateway lease regression tests (RED)

Added two focused coordinator regressions in `tests/test_gateway_lease_coordinator.py`:

- A production `GatewaySupervisor.ensure()` with no discoverable probe and no owner record should release the reservation as inactive.
- A non attach ready gateway with uncertain cleanup ownership must remain fail closed as `RECOVERY_REQUIRED` / `CLEANUP_UNVERIFIED`.

Targeted run:

```text
python -m unittest tests.test_gateway_lease_coordinator.GatewayLeaseCoordinatorTests.test_no_probe_without_owner_record_releases_reserved_lease_safely tests.test_gateway_lease_coordinator.GatewayLeaseCoordinatorTests.test_nonready_gateway_with_uncertain_owner_stays_fail_closed
```

Expected RED result (current production behavior): first test fails because the result is active (`RECOVERY_REQUIRED`, `CLEANUP_UNVERIFIED`) instead of inactive; second test passes, confirming fail closed behavior for uncertain ownership.

```text
Ran 2 tests in 0.035s
FAILED (failures=1)
```
