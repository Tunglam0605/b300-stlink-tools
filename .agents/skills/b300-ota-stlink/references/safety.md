# Safety and authorization

## Immutable invariants

| Flash area | Allowed action |
|---|---|
| Sector 0--2, `0x08000000..0x0800BFFF` | Never erase or program. |
| Sector 3 | Erased only as part of standard provision transaction. |
| Sector 4--7 | Application HEX only. |

Never use `mass_erase`, a chip erase, direct OpenOCD programming that bypasses
the CLI, Option Bytes/WRP modifications, or bootloader flashing through this
skill.

## Post-flash verification

Only when the user asks for hardware verification, read then resume the target.
Pass condition:

- `BKP1R` at `0x40002854` is zero;
- `BKP4R` at `0x40002860` is zero;
- PC lies within `0x08010000..0x0807FFFF`.

If verify fails or the board enters recovery, stop and preserve the log. Do not
retry, mass erase, or alter bootloader protection.
