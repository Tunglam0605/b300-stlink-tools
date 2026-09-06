# Large typed Watch Live batching design

## Problem

The Monitor tree enables **Add Watch Live** for compound DWARF variables, but
`xAgvInfor` from the current B300 AXF contains 167 watchable scalar descendants.
Those descendants require 72 distinct RAM words plus 12 64-bit coherence reads,
so the current 64-watch and 32-read limits reject the selection after the button
is clicked. The enabled button therefore promises an operation that cannot run.

The 32-read ceiling exists to bound one zero-halt SWD transaction and remains a
safety constraint. A large compound must be sampled across bounded transactions
instead of weakening that constraint.

## User-visible behavior

- Selecting a struct, union, or array adds every supported scalar descendant as
  one atomic operation, including `xAgvInfor` with 167 fields.
- The Monitor divides the selected watches into stable batches. Every batch uses
  at most 32 SWD word reads, including the DWT PC sample and repeated reads used
  to verify 64-bit coherence.
- One batch is read per configured interval. With the current 0.5 second
  interval and three batches, every `xAgvInfor` field is refreshed about every
  1.5 seconds.
- A sample updates only the rows contained in that batch. Values from other
  batches stay visible with their previous timestamp and quality state.
- The session status shows the active batch and total batch count when more than
  one batch is needed.
- If a single scalar cannot fit in one batch, the add operation fails atomically
  with a Vietnamese explanation and no rows are added.
- The Add button is disabled after a failed preflight for the same selection and
  is re-enabled when the user selects another node or the watch set changes.

## Core design

Add a deterministic typed-watch planner in `b300_core.variable_watch`. The
planner validates unique node IDs, unique display paths, DWARF type metadata,
RAM bounds, and the global typed-watch count before partitioning watches. It
preserves catalog order and packs contiguous watches into the current batch
until adding the next watch would exceed `MAX_LIVE_READ_WORDS`; then it starts a
new batch. Each candidate batch is checked by the existing compiled-watch
validator so the planner and transport share one safety definition.

The typed-watch ceiling becomes 512 to bound UI and decoding work. Manual CLI
watch specifications keep their existing 64-watch limit. A 512-field compound
is accepted only when each generated batch satisfies the 32-read ceiling.

`LiveMonitorRequest` carries the planned batches. `LiveMonitorController` starts
one continuous monitor session whose sampler advances through those batches in
round-robin order. The sampler returns partial `LiveSample` objects and records
the batch index/count as metadata. Existing single-batch requests behave exactly
as before.

The production panel continues to store rows by compiled watch identity. Partial
samples update matching rows only. History and export retain the real capture
timestamp for each value; they do not invent values for rows omitted from a
batch.

## Failure and lifecycle handling

- Planning is completed before the panel is mutated or hardware is opened.
- Duplicate, stale, unsupported, MMIO, or out-of-RAM descendants fail the same
  way as existing typed watches.
- Stop, cancellation, gateway loss, and cleanup apply to the whole round-robin
  session. No batch starts after cancellation is observed.
- Every batch reads the DWT PC sample and preserves the existing RUNNING/HALTED
  validation and zero-halt behavior.
- Remote Monitor uses the same plan after the SSH tunnel is ready; no new port or
  protocol is introduced.

## Verification

TDD coverage will include:

- A packed compound with more than 64 scalar fields is added atomically.
- The real shape of `xAgvInfor` (167 fields and an 84-read unbatched plan) splits
  into bounded batches and all fields eventually receive samples.
- Every batch stays within 32 reads including 64-bit coherence reads.
- Duplicate and invalid descendants cause no partial UI mutation.
- Partial samples preserve rows and timestamps belonging to other batches.
- Single-batch Monitor, manual CLI Watch Live, local transport, SSH transport,
  stop/cleanup, export, and existing flash safety tests remain green.

After implementation, run the complete Python test suite, compile checks,
release/version/package tests, and build smoke checks. Then test the connected
hardware in this order: `doctor --json`, probe/target identification, Monitor
sampling, local debug dry-run, bounded debug connection/cleanup, gateway health,
and application flash dry-run. Actual application flashing requires an exact
firmware file and probe selection and must preserve the Sector 0-2 protection,
Sector 3-7 transaction, metadata verification, and reset contract from
`AGENTS.md`.

Only after all required gates pass will version `0.21.2` be merged to `main`,
validated by Windows x64, Ubuntu x64, and Ubuntu ARM64 CI, tagged, published, and
verified through the signed updater metadata and stable download links.
