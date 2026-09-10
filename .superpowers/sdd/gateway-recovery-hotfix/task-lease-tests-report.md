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

GREEN implementation: `GatewaySupervisor.confirm_lease_owner_stopped()` now accepts a missing owner record as positive absence proof when no service is retained and the supervisor is stopped. Recorded or uncertain owners continue through the existing fail-closed proof path.

Validation after the change:

```text
Ran 2 tests in 0.036s
OK
Ran 22 tests in 0.420s
OK
```

Restart recovery coverage was extended for persisted leases with no owner record, plus malformed owner evidence. The malformed-record test was RED before the fix because `read()` conflated missing and invalid records; the implementation now checks path existence before accepting absence proof and preserves `RECOVERY_OWNER_UNPROVEN` for malformed evidence. A missing record with a stopped, service-less supervisor reconciles to `RECOVERY_RECONCILED` and clears the lease.

Final validation:

```text
Ran 3 tests in 0.099s
OK
Ran 24 tests in 0.478s
OK
```

Recovery review fix: `reconcile_lease_owner()` retains its original contract and returns false for missing or corrupt records. Coordinator recovery now performs a separate bounded `confirm_lease_owner_stopped()` absence proof; only confirmed absence permits `RECOVERY_RECONCILED`. Malformed/uncertain evidence remains `RECOVERY_REQUIRED`.

Final validation:

```text
Ran 61 tests in 16.991s
OK
```

Added coverage ensuring a valid owner record with unavailable process identity remains unreconciled even when endpoints appear closed. `confirm_lease_owner_stopped()` now requires a non-null immutable process identity before treating the process as gone.

Final validation:

```text
Ran 62 tests in 16.842s
OK
```
