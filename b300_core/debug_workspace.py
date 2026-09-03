"""Structured debugger workspace operations layered on verified B300 GDB/MI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .gdb_mi import GdbMiBackend, GdbMiCommandError


_MI_FIELD = re.compile(r'([A-Za-z0-9_-]+)="((?:\\.|[^"\\])*)"')
_SAFE_VAR_OBJECT = re.compile(r"^[A-Za-z0-9_.:-]+$")
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


@dataclass(frozen=True)
class DebugBreakpoint:
    number: int
    enabled: bool
    kind: str
    location: str
    address: Optional[str]
    hit_count: int = 0


class DebugWorkspaceBackend:
    """Structured Locals/Watch/Breakpoint facade for an active verified GDB session."""

    def __init__(self, gdb: GdbMiBackend) -> None:
        self.gdb = gdb

    def select_frame(self, level: int) -> int:
        selected = int(level)
        if not 0 <= selected <= 63:
            raise ValueError("Stack frame level must be in range 0..63.")
        self.gdb._request("-stack-select-frame %d" % selected, ("done",))
        return selected

    def list_locals(self) -> Tuple[DebugVariableNode, ...]:
        result = self.gdb._request("-stack-list-variables --simple-values", ("done",))
        variables = []
        for body in _balanced_objects(result.payload, "variable"):
            values = _fields(body)
            name = values.get("name", "")
            if not name:
                continue
            variables.append(DebugVariableNode(
                id="local:%s" % name, name=name, value=values.get("value", ""),
                type=values.get("type"), address=None, editable=True,
                has_children=False, children_loaded=True,
            ))
        return tuple(variables)

    def create_watch(self, expression: str) -> DebugVariableNode:
        self.gdb.evaluate_variable(expression)
        result = self.gdb._request('-var-create - * "%s"' % self.gdb._quote(expression), ("done",))
        values = _fields(result.payload)
        var_id = values.get("name")
        if not var_id or not _SAFE_VAR_OBJECT.fullmatch(var_id):
            raise GdbMiCommandError("GDB did not return a safe variable-object identifier.")
        try:
            child_count = int(values.get("numchild", "0"))
        except ValueError:
            child_count = 0
        return DebugVariableNode(
            id=var_id, name=expression, value=values.get("value", ""), type=values.get("type"),
            address=None, editable=True, has_children=child_count > 0, children_loaded=False,
        )

    def list_children(self, variable_id: str) -> Tuple[DebugVariableNode, ...]:
        selected = str(variable_id).strip()
        if not _SAFE_VAR_OBJECT.fullmatch(selected):
            raise ValueError("Variable-object identifier contains unsupported characters.")
        result = self.gdb._request(
            '-var-list-children --simple-values "%s"' % self.gdb._quote(selected), ("done",)
        )
        children = []
        for body in _balanced_objects(result.payload, "child"):
            values = _fields(body)
            child_id = values.get("name", "")
            if not child_id or not _SAFE_VAR_OBJECT.fullmatch(child_id):
                continue
            try:
                child_count = int(values.get("numchild", "0"))
            except ValueError:
                child_count = 0
            children.append(DebugVariableNode(
                id=child_id, name=values.get("exp") or child_id.rsplit(".", 1)[-1],
                value=values.get("value", ""), type=values.get("type"), address=None,
                editable=True, has_children=child_count > 0, children_loaded=False,
            ))
        return tuple(children)

    def update_watch(self, variable_id: str) -> DebugVariableNode:
        selected = str(variable_id).strip()
        if not _SAFE_VAR_OBJECT.fullmatch(selected):
            raise ValueError("Variable-object identifier contains unsupported characters.")
        result = self.gdb._request('-var-evaluate-expression "%s"' % self.gdb._quote(selected), ("done",))
        values = _fields(result.payload)
        return DebugVariableNode(
            id=selected, name=selected, value=values.get("value", ""), type=None,
            address=None, editable=True, has_children=False, children_loaded=False,
        )

    def assign_variable(self, variable_id: str, value: str) -> str:
        selected_id = str(variable_id).strip()
        selected_value = str(value).strip()
        if not _SAFE_VAR_OBJECT.fullmatch(selected_id):
            raise ValueError("Variable-object identifier contains unsupported characters.")
        if not _SAFE_ASSIGN_VALUE.fullmatch(selected_value):
            raise ValueError("Variable assignment accepts only a simple scalar/enum value.")
        result = self.gdb._request(
            '-var-assign "%s" "%s"' % (self.gdb._quote(selected_id), self.gdb._quote(selected_value)),
            ("done",),
        )
        values = _fields(result.payload)
        if "value" not in values:
            raise GdbMiCommandError("GDB did not confirm the assigned variable value.")
        return values["value"]

    def delete_watch(self, variable_id: str) -> None:
        selected = str(variable_id).strip()
        if not _SAFE_VAR_OBJECT.fullmatch(selected):
            raise ValueError("Variable-object identifier contains unsupported characters.")
        self.gdb._request('-var-delete "%s"' % self.gdb._quote(selected), ("done",))

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

    def set_breakpoint_enabled(self, number: int, enabled: bool) -> None:
        selected = int(number)
        if not 1 <= selected <= 9999:
            raise ValueError("Breakpoint number must be in range 1..9999.")
        command = "-break-enable" if enabled else "-break-disable"
        self.gdb._request("%s %d" % (command, selected), ("done",))
