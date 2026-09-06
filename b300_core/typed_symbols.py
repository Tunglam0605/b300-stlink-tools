"""Typed, offline variable catalog backed exclusively by ELF/AXF DWARF metadata."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    from elftools.dwarf.dwarf_expr import DWARFExprParser
    from elftools.elf.elffile import ELFFile
except ImportError:  # pragma: no cover - guarded by packaged requirements
    DWARFExprParser = None
    ELFFile = None


_RAM_RANGES = ((0x10000000, 0x10010000), (0x20000000, 0x20020000))
_WRAPPER_TAGS = {
    "DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type",
    "DW_TAG_restrict_type", "DW_TAG_atomic_type",
}
_CONTAINER_TAGS = {"DW_TAG_structure_type", "DW_TAG_union_type", "DW_TAG_array_type"}
_PAGE_LIMIT = 100


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def _attribute_text(die, name: str) -> str:
    attr = die.attributes.get(name)
    return _text(attr.value) if attr is not None else ""


def _attribute_int(die, name: str) -> Optional[int]:
    attr = die.attributes.get(name)
    if attr is None:
        return None
    try:
        return int(attr.value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class VariableNode:
    node_id: str
    name: str
    type_name: str
    kind: str
    address: Optional[int]
    byte_size: Optional[int]
    has_children: bool
    availability: str
    reason: Optional[str]
    path: str
    source_file: str
    value_type: Optional[str] = None
    bit_offset: Optional[int] = None
    bit_size: Optional[int] = None
    enum_values: Tuple[Tuple[int, str], ...] = ()
    type_identity: str = ""

    @property
    def watchable(self) -> bool:
        return self.availability == "watchable"


@dataclass(frozen=True)
class _NodeState:
    node: VariableNode
    type_die: object
    root_die_offset: int
    traversal: str
    dimensions: Tuple[int, ...] = ()
    depth: int = 0


class TypedSymbolCatalog:
    """Index global/static variables and lazily expand their DWARF type trees."""

    def __init__(self, image: Path) -> None:
        if ELFFile is None or DWARFExprParser is None:
            raise RuntimeError("Typed AXF/ELF browsing requires pyelftools.")
        self.image = Path(image).expanduser().resolve()
        if self.image.suffix.lower() not in {".elf", ".axf"} or not self.image.is_file():
            raise ValueError("Typed symbols require an existing ELF/AXF file.")
        data = self.image.read_bytes()
        self.fingerprint = hashlib.sha256(data).hexdigest()
        try:
            self._stream = io.BytesIO(data)
            self._elf = ELFFile(self._stream)
        except Exception as error:
            raise ValueError("Unable to parse ELF/AXF image: %s" % error) from error
        if not self._elf.has_dwarf_info():
            raise RuntimeError("ELF/AXF does not contain DWARF debug information; types cannot be inferred.")
        self._dwarf = self._elf.get_dwarf_info()
        self.pointer_size = self._elf.elfclass // 8
        self._states: Dict[str, _NodeState] = {}
        self._root_states = []
        self._index_roots()

    @staticmethod
    def _validate_page(offset: int, limit: int) -> Tuple[int, int]:
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("Typed symbol page offset must be a non-negative integer.")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _PAGE_LIMIT:
            raise ValueError("Typed symbol page limit must be 1..%d." % _PAGE_LIMIT)
        return offset, limit

    @staticmethod
    def _referenced_die(die, name: str = "DW_AT_type"):
        if name not in die.attributes:
            return None
        try:
            return die.get_DIE_from_attribute(name)
        except Exception:
            return None

    def _canonical_die(self, die):
        seen = set()
        current = die
        while current is not None and current.tag in _WRAPPER_TAGS:
            if current.offset in seen:
                break
            seen.add(current.offset)
            current = self._referenced_die(current)
        return current

    def _sizeof(self, die) -> Optional[int]:
        seen = set()
        current = die
        while current is not None and current.offset not in seen:
            seen.add(current.offset)
            value = _attribute_int(current, "DW_AT_byte_size")
            if value is not None:
                return value
            if current.tag == "DW_TAG_pointer_type":
                return int(self.pointer_size)
            if current.tag == "DW_TAG_array_type":
                element = self._referenced_die(current)
                element_size = self._sizeof(element)
                dimensions = self._array_dimensions(current)
                if element_size is not None and dimensions:
                    total = element_size
                    for count in dimensions:
                        total *= count
                    return total
            if current.tag not in _WRAPPER_TAGS:
                return None
            current = self._referenced_die(current)
        return None

    @staticmethod
    def _array_dimensions(die) -> Tuple[int, ...]:
        result = []
        for child in die.iter_children():
            if child.tag != "DW_TAG_subrange_type":
                continue
            count = _attribute_int(child, "DW_AT_count")
            if count is None:
                upper = _attribute_int(child, "DW_AT_upper_bound")
                lower = _attribute_int(child, "DW_AT_lower_bound") or 0
                count = None if upper is None else upper - lower + 1
            if count is None or count < 0:
                return ()
            result.append(count)
        return tuple(result)

    def _expression_value(self, die, attr_name: str, expected_op: str) -> Optional[int]:
        attr = die.attributes.get(attr_name)
        if attr is None or attr.form not in {"DW_FORM_block", "DW_FORM_block1", "DW_FORM_block2",
                                             "DW_FORM_block4", "DW_FORM_exprloc"}:
            return None
        try:
            ops = DWARFExprParser(die.cu.structs).parse_expr(bytes(attr.value))
        except Exception:
            return None
        if len(ops) != 1 or ops[0].op_name != expected_op or len(ops[0].args) != 1:
            return None
        return int(ops[0].args[0])

    def _address(self, die) -> Optional[int]:
        return self._expression_value(die, "DW_AT_location", "DW_OP_addr")

    def _member_offset(self, die, *, union: bool) -> Optional[int]:
        value = _attribute_int(die, "DW_AT_data_member_location")
        if value is not None:
            return value
        value = self._expression_value(die, "DW_AT_data_member_location", "DW_OP_plus_uconst")
        if value is not None:
            return value
        return 0 if union and "DW_AT_data_member_location" not in die.attributes else None

    def _type_name(self, die) -> str:
        if die is None:
            return "<unknown>"
        named = _attribute_text(die, "DW_AT_name")
        if named:
            return named
        canonical = self._canonical_die(die)
        if canonical is not None and canonical.tag == "DW_TAG_array_type":
            element = self._referenced_die(canonical)
            base = self._type_name(element)
            return base + "".join("[%d]" % count for count in self._array_dimensions(canonical))
        if canonical is not None:
            named = _attribute_text(canonical, "DW_AT_name")
            if named:
                return named
            return "<anonymous %s>" % canonical.tag.removeprefix("DW_TAG_").removesuffix("_type")
        return "<unknown>"

    def _kind(self, die, dimensions: Tuple[int, ...] = ()) -> str:
        if dimensions:
            return "array"
        canonical = self._canonical_die(die)
        if canonical is None:
            return "unsupported"
        return {
            "DW_TAG_structure_type": "structure",
            "DW_TAG_union_type": "union",
            "DW_TAG_array_type": "array",
            "DW_TAG_pointer_type": "pointer",
            "DW_TAG_enumeration_type": "enum",
            "DW_TAG_base_type": "scalar",
        }.get(canonical.tag, "unsupported")

    def _enum_values(self, die) -> Tuple[Tuple[int, str], ...]:
        canonical = self._canonical_die(die)
        if canonical is None or canonical.tag != "DW_TAG_enumeration_type":
            return ()
        values = []
        for child in canonical.iter_children():
            if child.tag != "DW_TAG_enumerator":
                continue
            name = _attribute_text(child, "DW_AT_name")
            value = _attribute_int(child, "DW_AT_const_value")
            if name and value is not None:
                values.append((value, name))
        return tuple(values)

    def _value_type(self, die) -> Optional[str]:
        canonical = self._canonical_die(die)
        if canonical is None:
            return None
        size = self._sizeof(canonical)
        if canonical.tag == "DW_TAG_pointer_type":
            return "u32" if size == 4 else None
        if canonical.tag == "DW_TAG_enumeration_type":
            signed = any(value < 0 for value, _name in self._enum_values(canonical))
            return ("i" if signed else "u") + str(size * 8) if size in (1, 2, 4) else None
        if canonical.tag != "DW_TAG_base_type":
            return None
        encoding = _attribute_int(canonical, "DW_AT_encoding")
        if encoding == 0x04:
            return {4: "f32", 8: "f64"}.get(size)
        if encoding in (0x05, 0x06):
            return {1: "i8", 2: "i16", 4: "i32"}.get(size)
        if encoding in (0x02, 0x07, 0x08):
            return {1: "u8", 2: "u16", 4: "u32"}.get(size)
        return None

    @staticmethod
    def _inside_ram(address: Optional[int], size: Optional[int]) -> bool:
        if address is None or size is None or size <= 0:
            return False
        end = address + size
        return any(start <= address and end <= limit for start, limit in _RAM_RANGES)

    def _node_id(self, root_offset: int, traversal: str, type_die, address: Optional[int]) -> str:
        identity = "%s|%X|%s|%s|%s" % (
            self.fingerprint, root_offset, traversal,
            "none" if type_die is None else "%X" % type_die.offset,
            "none" if address is None else "%X" % address,
        )
        return "typed:%s:%s" % (self.fingerprint, hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24])

    def _make_state(self, *, name: str, type_die, address: Optional[int], path: str,
                    source_file: str, root_offset: int, traversal: str,
                    dimensions: Tuple[int, ...] = (), depth: int = 0,
                    bit_offset: Optional[int] = None, bit_size: Optional[int] = None,
                    storage_size: Optional[int] = None) -> _NodeState:
        canonical = self._canonical_die(type_die)
        if not dimensions and canonical is not None and canonical.tag == "DW_TAG_array_type":
            dimensions = self._array_dimensions(canonical)
        kind = self._kind(type_die, dimensions)
        byte_size = storage_size if storage_size is not None else self._sizeof(type_die)
        has_children = kind in {"structure", "union"} or (kind == "array" and bool(dimensions))
        value_type = self._value_type(type_die)
        enum_values = self._enum_values(type_die)
        if has_children:
            availability = "browse_only"
            reason = "Expand to select a scalar member."
        elif kind == "pointer" and value_type is not None and self._inside_ram(address, byte_size):
            availability = "watchable"
            reason = "Pointer address only. Pointee is not dereferenced for zero-halt safety."
        elif value_type is None:
            availability = "unavailable"
            reason = "DWARF type is unsupported or incomplete; Live Watch type will not be guessed."
        elif not self._inside_ram(address, byte_size):
            availability = "unavailable"
            reason = "Variable byte span is not fully inside STM32F407 CCM/SRAM."
        else:
            availability = "watchable"
            reason = None
        node_id = self._node_id(root_offset, traversal, type_die, address)
        type_offset = "none" if type_die is None else "0x%X" % type_die.offset
        node = VariableNode(
            node_id=node_id, name=name, type_name=self._type_name(type_die), kind=kind,
            address=address, byte_size=byte_size, has_children=has_children,
            availability=availability, reason=reason, path=path, source_file=source_file,
            value_type=value_type, bit_offset=bit_offset, bit_size=bit_size,
            enum_values=enum_values, type_identity=type_offset,
        )
        state = _NodeState(node, type_die, root_offset, traversal, dimensions, depth)
        self._states[node_id] = state
        return state

    def _index_roots(self) -> None:
        for cu in self._dwarf.iter_CUs():
            top = cu.get_top_DIE()
            source_file = _attribute_text(top, "DW_AT_name")

            def visit(parent, ancestors: Tuple[str, ...]) -> None:
                for die in parent.iter_children():
                    if die.tag == "DW_TAG_variable":
                        name = _attribute_text(die, "DW_AT_name")
                        declaration = bool(_attribute_int(die, "DW_AT_declaration") or 0)
                        address = self._address(die)
                        at_file_scope = not ancestors or ancestors[-1] in {
                            "DW_TAG_compile_unit", "DW_TAG_namespace",
                        }
                        # Address-bearing variables nested in a subprogram are static locals.
                        if name and not declaration and (at_file_scope or address is not None):
                            type_die = self._referenced_die(die)
                            state = self._make_state(
                                name=name, type_die=type_die, address=address, path=name,
                                source_file=source_file, root_offset=int(die.offset),
                                traversal="root", depth=0,
                            )
                            self._root_states.append(state)
                    if die.has_children:
                        visit(die, ancestors + (die.tag,))

            visit(top, (top.tag,))
        self._root_states.sort(key=lambda state: (
            state.node.name.casefold(), state.node.name, state.node.source_file,
            -1 if state.node.address is None else state.node.address, state.root_die_offset,
        ))

    def roots(self, query: str = "", offset: int = 0, limit: int = 100) -> Tuple[VariableNode, ...]:
        offset, limit = self._validate_page(offset, limit)
        needle = str(query).strip().casefold()
        selected = [state.node for state in self._root_states if not needle or needle in (
            state.node.name + " " + state.node.type_name + " " + state.node.source_file
        ).casefold()]
        return tuple(selected[offset:offset + limit])

    def node(self, node_id: str) -> VariableNode:
        state = self._states.get(str(node_id))
        if state is None:
            raise ValueError("Typed symbol node is stale or does not belong to this AXF/ELF catalog.")
        return state.node

    def children(self, node_id: str, offset: int = 0, limit: int = 100) -> Tuple[VariableNode, ...]:
        offset, limit = self._validate_page(offset, limit)
        state = self._states.get(str(node_id))
        if state is None:
            raise ValueError("Typed symbol node is stale or does not belong to this AXF/ELF catalog.")
        if state.depth >= 8:
            return ()
        node = state.node
        canonical = self._canonical_die(state.type_die)
        if canonical is None:
            return ()
        results = []
        if state.dimensions:
            count = state.dimensions[0]
            element_type = self._referenced_die(canonical) if canonical.tag == "DW_TAG_array_type" else state.type_die
            element_size = self._sizeof(element_type)
            remaining = state.dimensions[1:]
            stride = element_size
            if stride is not None:
                for dimension in remaining:
                    stride *= dimension
            for index in range(offset, min(count, offset + limit)):
                child_address = None if node.address is None or stride is None else node.address + index * stride
                child_name = "[%d]" % index
                child_path = node.path + child_name
                results.append(self._make_state(
                    name=child_name, type_die=element_type, address=child_address,
                    path=child_path, source_file=node.source_file,
                    root_offset=state.root_die_offset,
                    traversal=state.traversal + child_name, dimensions=remaining,
                    depth=state.depth + 1,
                ).node)
            return tuple(results)
        if canonical.tag not in {"DW_TAG_structure_type", "DW_TAG_union_type"}:
            return ()
        members = [child for child in canonical.iter_children() if child.tag == "DW_TAG_member"]
        is_union = canonical.tag == "DW_TAG_union_type"
        for member in members[offset:offset + limit]:
            name = _attribute_text(member, "DW_AT_name") or "<anonymous>"
            member_type = self._referenced_die(member)
            member_offset = self._member_offset(member, union=is_union)
            address = None if node.address is None or member_offset is None else node.address + member_offset
            bit_size = _attribute_int(member, "DW_AT_bit_size")
            bit_offset = None
            storage_size = _attribute_int(member, "DW_AT_byte_size") or self._sizeof(member_type)
            data_bit_offset = _attribute_int(member, "DW_AT_data_bit_offset")
            legacy_bit_offset = _attribute_int(member, "DW_AT_bit_offset")
            if bit_size is not None:
                if data_bit_offset is not None:
                    if address is not None:
                        address += data_bit_offset // 8
                    bit_offset = data_bit_offset % 8
                elif legacy_bit_offset is not None and storage_size is not None:
                    bit_offset = storage_size * 8 - legacy_bit_offset - bit_size
            path = node.path + ("." if name != "<anonymous>" else ".") + name
            results.append(self._make_state(
                name=name, type_die=member_type, address=address, path=path,
                source_file=node.source_file, root_offset=state.root_die_offset,
                traversal=state.traversal + ".%X" % member.offset,
                depth=state.depth + 1, bit_offset=bit_offset, bit_size=bit_size,
                storage_size=storage_size if bit_size is not None else None,
            ).node)
        return tuple(results)


__all__ = ["TypedSymbolCatalog", "VariableNode"]
