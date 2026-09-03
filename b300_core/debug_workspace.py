"""Structured debugger workspace operations layered on verified B300 GDB/MI."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional, Tuple

from .gdb_mi import GdbMiBackend, GdbMiCommandError


_MI_FIELD = re.compile(r'([A-Za-z0-9_-]+)="((?:\\.|[^"\\])*)"')
_SAFE_VAR_OBJECT = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SAFE_LOCAL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_ASSIGN_VALUE = re.compile(
    r"^(?:"
    r"[+-]?(?:0[xX][0-9A-Fa-f]+|[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?)(?:[uUlLfF]*)"
    r"|true|false|[A-Za-z_][A-Za-z0-9_]*"
    r")$"
)


def _decode(value: str) -> str:
    return bytes(value, "utf-8").decode("unicode_escape")


def _fields(payload: str) -> dict:
    return {key: _decode(value) for key, value in _MI_FIELD.findall(payload)}


def _balanced_objects(payload: str, marker: str) -> Tuple[str, ...]:
    needle = marker + "={"
    results = []
    cursor = 0
    while True:
        start = payload.find(needle, cursor)
        if start < 0:
            return tuple(results)
        body_start = start + len(marker) + 1
        depth = 0
        quoted = False
        escaped = False
        end = None
        for index in range(body_start, len(payload)):
            char = payload[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
                continue
            if char == '"':
                quoted = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end is None:
            raise GdbMiCommandError("GDB returned malformed nested MI data for %s." % marker)
        results.append(payload[body_start + 1:end])
        cursor = end + 1


def _list_objects(payload: str, key: str) -> Tuple[str, ...]:
    marker = key + "=["
    start = payload.find(marker)
    if start < 0:
        return ()
    cursor = start + len(marker)
    depth = 0
    quoted = False
    escaped = False
    body_start = None
    results = []
    for index in range(cursor, len(payload)):
        char = payload[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
            continue
        if char == "{":
            if depth == 0:
                body_start = index + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and body_start is not None:
                results.append(payload[body_start:index])
                body_start = None
        elif char == "]" and depth == 0:
            break
    if depth != 0:
        raise GdbMiCommandError("GDB returned malformed MI list for %s." % key)
    return tuple(results)


@dataclass(frozen=True)
class DebugVariableNode:
    id: str
    name: str
    value: str
    type: Optional[str]
    address: Optional[str]
    editable: bool
    has_children: bool
    children_loaded: bool = False
    changed: bool = False
    in_scope: bool = True


@dataclass(frozen=True)
class DebugRegister:
    name: str
    value: str
    changed: bool = False


@dataclass(frozen=True)
class DebugBreakpoint:
    number: int
    enabled: bool
    kind: str
    location: str
    address: Optional[str]
    hit_count: int = 0


@dataclass(frozen=True)
class DebugBreakpointUsage:
    breakpoints: int
    breakpoint_limit: int
    watchpoints: int
    watchpoint_limit: int


@dataclass(frozen=True)
class DebugSourceLocation:
    function: Optional[str]
    file: Optional[str]
    fullname: Optional[str]
    line: Optional[int]
    address: Optional[int]


TargetStateProvider = Callable[[], str]


class DebugWorkspaceBackend:
    """Structured Locals/Watch/Register/Breakpoint facade for an active GDB session.

    Mutating variable operations are deliberately enabled only while the target is HALTED.
    The GUI can therefore expose edit-in-place without introducing arbitrary GDB commands.
    """

    def __init__(self, gdb: GdbMiBackend,
                 target_state_provider: Optional[TargetStateProvider] = None) -> None:
        self.gdb = gdb
        self._target_state_provider = target_state_provider
        self._variables: Dict[str, DebugVariableNode] = {}
        self._local_ids: Dict[str, str] = {}
        self._last_registers: Dict[str, str] = {}

    @property
    def target_state(self) -> str:
        if self._target_state_provider is None:
            return "unknown"
        try:
            return str(self._target_state_provider()).strip().lower() or "unknown"
        except Exception:
            return "unknown"

    @property
    def variable_editable(self) -> bool:
        return self.target_state == "halted"

    def _require_halted(self, action: str) -> None:
        if not self.variable_editable:
            raise RuntimeError("%s requires a HALTED target." % action)

    @staticmethod
    def _validate_variable_id(variable_id: str) -> str:
        selected = str(variable_id).strip()
        if not _SAFE_VAR_OBJECT.fullmatch(selected):
            raise ValueError("Variable-object identifier contains unsupported characters.")
        return selected

    def _node_from_fields(self, values: dict, *, display_name: Optional[str] = None,
                          children_loaded: bool = False) -> DebugVariableNode:
        var_id = values.get("name")
        if not var_id or not _SAFE_VAR_OBJECT.fullmatch(var_id):
            raise GdbMiCommandError("GDB did not return a safe variable-object identifier.")
        try:
            child_count = int(values.get("numchild", values.get("new_num_children", "0")))
        except ValueError:
            child_count = 0
        node = DebugVariableNode(
            id=var_id,
            name=display_name or values.get("exp") or var_id.rsplit(".", 1)[-1],
            value=values.get("value", ""),
            type=values.get("type") or values.get("new_type"),
            address=values.get("addr"),
            editable=self.variable_editable,
            has_children=child_count > 0,
            children_loaded=children_loaded,
            changed=False,
            in_scope=values.get("in_scope", "true").lower() not in {"false", "invalid"},
        )
        self._variables[node.id] = node
        return node

    def _create_variable_object(self, expression: str, *, display_name: Optional[str] = None) -> DebugVariableNode:
        self.gdb.evaluate_variable(expression)
        result = self.gdb._request(
            '-var-create - * "%s"' % self.gdb._quote(expression), ("done",)
        )
        return self._node_from_fields(_fields(result.payload), display_name=display_name)

    def _delete_variable_object_best_effort(self, variable_id: str) -> None:
        try:
            self.gdb._request('-var-delete "%s"' % self.gdb._quote(variable_id), ("done",))
        except Exception:
            pass
        self._variables.pop(variable_id, None)

    def _clear_local_objects(self) -> None:
        for variable_id in tuple(self._local_ids.values()):
            self._delete_variable_object_best_effort(variable_id)
        self._local_ids.clear()

    def select_frame(self, level: int) -> int:
        selected = int(level)
        if not 0 <= selected <= 63:
            raise ValueError("Stack frame level must be in range 0..63.")
        self.gdb._request("-stack-select-frame %d" % selected, ("done",))
        self._clear_local_objects()
        return selected

    def current_location(self) -> DebugSourceLocation:
        frame = self.gdb.current_frame()
        return DebugSourceLocation(
            frame.function, frame.file, frame.fullname, frame.line, frame.address,
        )

    def call_stack(self, max_frames: int = 16):
        return self.gdb.stack_frames(max_frames)

    def registers(self) -> Tuple[DebugRegister, ...]:
        rows = []
        current = {}
        for item in self.gdb.register_values():
            current[item.name] = item.value
            rows.append(DebugRegister(
                item.name, item.value,
                changed=item.name in self._last_registers and self._last_registers[item.name] != item.value,
            ))
        self._last_registers = current
        return tuple(rows)

    def list_locals(self) -> Tuple[DebugVariableNode, ...]:
        result = self.gdb._request("-stack-list-variables --simple-values", ("done",))
        raw = []
        for body in _balanced_objects(result.payload, "variable"):
            values = _fields(body)
            name = values.get("name", "")
            if name and _SAFE_LOCAL_NAME.fullmatch(name):
                raw.append((name, values))

        active_names = {name for name, _values in raw}
        for stale in tuple(set(self._local_ids) - active_names):
            self._delete_variable_object_best_effort(self._local_ids.pop(stale))

        rows = []
        for name, values in raw:
            existing_id = self._local_ids.get(name)
            if existing_id and existing_id in self._variables:
                try:
                    rows.append(self.refresh_variable(existing_id, display_name=name))
                    continue
                except Exception:
                    self._delete_variable_object_best_effort(existing_id)
                    self._local_ids.pop(name, None)
            try:
                node = self._create_variable_object(name, display_name=name)
                self._local_ids[name] = node.id
                rows.append(node)
            except GdbMiCommandError:
                rows.append(DebugVariableNode(
                    id="local:%s" % name, name=name, value=values.get("value", ""),
                    type=values.get("type"), address=None, editable=False,
                    has_children=False, children_loaded=True,
                ))
        return tuple(rows)

    def create_watch(self, expression: str) -> DebugVariableNode:
        return self._create_variable_object(expression, display_name=expression)

    def list_children(self, variable_id: str) -> Tuple[DebugVariableNode, ...]:
        selected = self._validate_variable_id(variable_id)
        result = self.gdb._request(
            '-var-list-children --simple-values "%s"' % self.gdb._quote(selected), ("done",)
        )
        children = []
        for body in _balanced_objects(result.payload, "child"):
            values = _fields(body)
            try:
                children.append(self._node_from_fields(values))
            except GdbMiCommandError:
                continue
        parent = self._variables.get(selected)
        if parent is not None:
            self._variables[selected] = replace(parent, children_loaded=True)
        return tuple(children)

    def refresh_variable(self, variable_id: str,
                         *, display_name: Optional[str] = None) -> DebugVariableNode:
        selected = self._validate_variable_id(variable_id)
        result = self.gdb._request(
            '-var-evaluate-expression "%s"' % self.gdb._quote(selected), ("done",)
        )
        values = _fields(result.payload)
        previous = self._variables.get(selected)
        if previous is None:
            previous = DebugVariableNode(
                selected, display_name or selected, "", None, None,
                self.variable_editable, False,
            )
        updated = replace(
            previous,
            name=display_name or previous.name,
            value=values.get("value", previous.value),
            editable=self.variable_editable,
            changed=values.get("value", previous.value) != previous.value,
        )
        self._variables[selected] = updated
        return updated

    def refresh_changes(self) -> Tuple[DebugVariableNode, ...]:
        result = self.gdb._request("-var-update --all-values *", ("done",))
        changed = []
        for body in _list_objects(result.payload, "changelist"):
            values = _fields(body)
            variable_id = values.get("name", "")
            if not variable_id or not _SAFE_VAR_OBJECT.fullmatch(variable_id):
                continue
            previous = self._variables.get(variable_id)
            if previous is None:
                continue
            try:
                child_count = int(values.get("new_num_children", "1" if previous.has_children else "0"))
            except ValueError:
                child_count = 1 if previous.has_children else 0
            node = replace(
                previous,
                value=values.get("value", previous.value),
                type=values.get("new_type") or previous.type,
                editable=self.variable_editable,
                has_children=child_count > 0,
                changed=True,
                in_scope=values.get("in_scope", "true").lower() not in {"false", "invalid"},
            )
            self._variables[variable_id] = node
            changed.append(node)
        return tuple(changed)

    def assign_variable(self, variable_id: str, value: str) -> str:
        self._require_halted("Variable assignment")
        selected_id = self._validate_variable_id(variable_id)
        selected_value = str(value).strip()
        if not _SAFE_ASSIGN_VALUE.fullmatch(selected_value):
            raise ValueError("Variable assignment accepts only a simple scalar/enum value.")
        result = self.gdb._request(
            '-var-assign "%s" "%s"' % (self.gdb._quote(selected_id), self.gdb._quote(selected_value)),
            ("done",),
        )
        values = _fields(result.payload)
        if "value" not in values:
            raise GdbMiCommandError("GDB did not confirm the assigned variable value.")
        previous = self._variables.get(selected_id)
        if previous is not None:
            self._variables[selected_id] = replace(
                previous, value=values["value"], changed=True, editable=True,
            )
        return values["value"]

    def delete_watch(self, variable_id: str) -> None:
        selected = self._validate_variable_id(variable_id)
        self.gdb._request('-var-delete "%s"' % self.gdb._quote(selected), ("done",))
        self._variables.pop(selected, None)
        for name, local_id in tuple(self._local_ids.items()):
            if local_id == selected:
                self._local_ids.pop(name, None)

    def list_breakpoints(self) -> Tuple[DebugBreakpoint, ...]:
        result = self.gdb._request("-break-list", ("done",))
        rows = []
        for body in _balanced_objects(result.payload, "bkpt"):
            values = _fields(body)
            number_text = values.get("number")
            if not number_text or not number_text.isdigit():
                continue
            location = values.get("original-location") or values.get("func") or values.get("what") or ""
            try:
                hits = int(values.get("times", "0"))
            except ValueError:
                hits = 0
            rows.append(DebugBreakpoint(
                number=int(number_text), enabled=values.get("enabled", "y").lower() == "y",
                kind=values.get("type", "breakpoint"), location=location,
                address=values.get("addr"), hit_count=hits,
            ))
        return tuple(rows)

    def breakpoint_usage(self, *, breakpoint_limit: int = 6,
                         watchpoint_limit: int = 4) -> DebugBreakpointUsage:
        rows = self.list_breakpoints()
        watchpoints = sum(1 for row in rows if "watch" in row.kind.lower())
        breakpoints = len(rows) - watchpoints
        return DebugBreakpointUsage(
            breakpoints, breakpoint_limit, watchpoints, watchpoint_limit,
        )

    def create_hardware_breakpoint(self, location: str) -> int:
        return self.gdb.insert_hardware_breakpoint(location).number

    def create_watchpoint(self, expression: str) -> int:
        return self.gdb.insert_watchpoint(expression).number

    def delete_breakpoint(self, number: int) -> None:
        self.gdb.delete_breakpoint(int(number))

    def set_breakpoint_enabled(self, number: int, enabled: bool) -> None:
        selected = int(number)
        if not 1 <= selected <= 9999:
            raise ValueError("Breakpoint number must be in range 1..9999.")
        command = "-break-enable" if enabled else "-break-disable"
        self.gdb._request("%s %d" % (command, selected), ("done",))

    def step_out(self, timeout_seconds: float = 5.0) -> DebugSourceLocation:
        self._require_halted("Step Out")
        if not 0.1 <= float(timeout_seconds) <= 60.0:
            raise ValueError("Step Out timeout must be in range 0.1..60 seconds.")
        start_index = len(self.gdb.async_records)
        self.gdb._request("-exec-finish", ("running", "done"))
        self.gdb.wait_for_stopped(start_index=start_index, timeout_seconds=timeout_seconds)
        return self.current_location()

    def close(self) -> None:
        self._clear_local_objects()
        for variable_id in tuple(self._variables):
            self._delete_variable_object_best_effort(variable_id)
        self._variables.clear()
