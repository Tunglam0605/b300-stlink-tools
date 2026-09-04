# Debug Workbench Layout Reference

This file is the acceptance reference for the v0.17 debugger UI.

```text
+--------------------------------------------------------------------------------+
| Debug menu | View menu | Window menu                                             |
+--------------------------------------------------------------------------------+
| LOCAL | STM32F407 | GDB ● | TCL ● | MCU HALT | PC | sample/trace status         |
+--------------------------------------------------------------------------------+
| Run | Halt | Reset | Step Into | Step Over | Step Out | Break | Disconnect       |
+----------------------+-----------------------------------------+-------------------+
| Navigation           | Source / Disassembly / Trace            | Inspect           |
| [Symbols] [Stack]    |                                         | [Watch][Regs][SVD]|
|                      |               EDITOR                    |                   |
|                      |                                         |                   |
+----------------------+-----------------------------------------+-------------------+
| Debug Tools: [Breakpoints][Live Watch][Memory][Console][Log][Fault][FreeRTOS]   |
+--------------------------------------------------------------------------------+
```

Acceptance rules:

- Source is the primary center surface.
- Left/right/bottom panes are dockable and can be hidden from `View`.
- `Window > Reset Debug Layout` restores a deterministic default.
- The normal zero-halt Studio live panel is never reparented into this workbench.
- Technical detail is grouped in tabs/docks rather than stacked dashboard cards.
- The workbench must remain usable at 1366x768 and high-DPI Windows scaling.
- The layout may be saved/restored without changing debug controller state.
