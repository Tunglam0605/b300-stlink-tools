# B300 Native Debug Core

C++17 data-plane foundation for future high-rate trace/sampling workloads.

This module is independent of Qt, Python, OpenOCD policy, Flash/OTA logic and target-control policy. Python remains the control plane and safety owner.

Initial scope:
- bounded sample/event buffers;
- transport-neutral trace/sample DTOs;
- deterministic drain semantics;
- explicit dropped-item accounting;
- native benchmarks/tests.

Python binding (pybind11) is intentionally deferred to the integration branch after the native API is stable and parity tests exist.
