"""Read-only DWARF type/source introspection for verified ELF/AXF images.

The service is deliberately offline: it never talks to the target, never mutates
GDB state and never guesses structure offsets when DWARF is missing or ambiguous.
"""

from __future__ import annotations

import io
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

try:
    from elftools.dwarf.descriptions import describe_form_class
    from elftools.elf.elffile import ELFFile
except ImportError:  # pragma: no cover - exercised by packaging/runtime guard
    ELFFile = None
    describe_form_class = None


_TYPE_TAGS = {
    "DW_TAG_base_type",
    "DW_TAG_pointer_type",
    "DW_TAG_array_type",
    "DW_TAG_structure_type",
    "DW_TAG_union_type",
    "DW_TAG_typedef",
    "DW_TAG_enumeration_type",
    "DW_TAG_const_type",
    "DW_TAG_volatile_type",
    "DW_TAG_restrict_type",
    "DW_TAG_atomic_type",
}
_WRAPPER_TAGS = {
    "DW_TAG_typedef",
    "DW_TAG_const_type",
    "DW_TAG_volatile_type",
    "DW_TAG_restrict_type",
    "DW_TAG_atomic_type",
}


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _attr_text(die, name: str) -> str:
    attr = die.attributes.get(name)
    return "" if attr is None else _text(attr.value)


@dataclass(frozen=True)
class DwarfMember:
    name: str
    offset: Optional[int]
    type_die_offset: Optional[int]
    type_name: str
    byte_size: Optional[int]


@dataclass(frozen=True)
class DwarfEnumValue:
    name: str
    value: int


@dataclass(frozen=True)
class DwarfTypeInfo:
    die_offset: int
    name: str
    kind: str
    byte_size: Optional[int]
    target_die_offset: Optional[int] = None
    element_die_offset: Optional[int] = None
    element_count: Optional[int] = None
    members: Tuple[DwarfMember, ...] = ()
    enum_values: Tuple[DwarfEnumValue, ...] = ()


@dataclass(frozen=True)
class DwarfSourceLocation:
    address: int
    function: Optional[str]
    file: Optional[str]
    line: Optional[int]


class DwarfTypeService:
    """Resolve DWARF types, structure offsets and source lines without guessing."""

    def __init__(self, image: Path) -> None:
        if ELFFile is None:
            raise RuntimeError("DWARF support requires pyelftools.")
        path = Path(image).expanduser().resolve()
        if path.suffix.lower() not in {".elf", ".axf"}:
            raise ValueError("DWARF image must be an ELF or AXF file.")
        if not path.is_file():
            raise ValueError("DWARF image does not exist: %s" % path)
        try:
            data = path.read_bytes()
            self._stream = io.BytesIO(data)
            self.elf = ELFFile(self._stream)
        except Exception as error:
            raise ValueError("Unable to parse ELF/AXF image: %s" % error) from error
        if not self.elf.has_dwarf_info():
            raise RuntimeError("ELF/AXF does not contain DWARF debug information.")
        self.path = path
        self.dwarf = self.elf.get_dwarf_info()
        self.pointer_size = int(self.elf.elfclass // 8)
        self._dies: Dict[int, object] = {}
        self._named_types: Dict[str, list[int]] = {}
        self._variables: Dict[str, list[int]] = {}
        self._cache: Dict[int, DwarfTypeInfo] = {}
        self._line_rows: list[tuple[int, str, int]] = []
        self._line_addresses: list[int] = []
        self._functions: list[tuple[int, int, str]] = []
        self._index()

    @property
    def has_debug_info(self) -> bool:
        return True

    def _index(self) -> None:
        for cu in self.dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                self._dies[int(die.offset)] = die
                name = _attr_text(die, "DW_AT_name")
                if name and die.tag in _TYPE_TAGS:
                    self._named_types.setdefault(name, []).append(int(die.offset))
                if name and die.tag == "DW_TAG_variable":
                    self._variables.setdefault(name, []).append(int(die.offset))
                if die.tag == "DW_TAG_subprogram":
                    self._index_function(die)
            self._index_lines(cu)
        self._line_rows.sort(key=lambda item: item[0])
        self._line_addresses = [row[0] for row in self._line_rows]
        self._functions.sort(key=lambda item: (item[0], item[1]))

    def _index_function(self, die) -> None:
        low_attr = die.attributes.get("DW_AT_low_pc")
        high_attr = die.attributes.get("DW_AT_high_pc")
        if low_attr is None or high_attr is None:
            return
        try:
            low = int(low_attr.value)
            form_class = describe_form_class(high_attr.form)
            high = int(high_attr.value) if form_class == "address" else low + int(high_attr.value)
        except Exception:
            return
        if high <= low:
            return
        name = _attr_text(die, "DW_AT_name") or _attr_text(die, "DW_AT_linkage_name")
        self._functions.append((low, high, name or "<anonymous>"))

    def _index_lines(self, cu) -> None:
        try:
            lineprog = self.dwarf.line_program_for_CU(cu)
        except Exception:
            lineprog = None
        if lineprog is None:
            return
        top = cu.get_top_DIE()
        comp_dir = _attr_text(top, "DW_AT_comp_dir")
        try:
            file_entries = lineprog["file_entry"]
            include_dirs = lineprog["include_directory"]
        except Exception:
            file_entries = getattr(lineprog.header, "file_entry", ())
            include_dirs = getattr(lineprog.header, "include_directory", ())

        def file_name(file_index: int) -> str:
            if file_index <= 0 or file_index > len(file_entries):
                return ""
            entry = file_entries[file_index - 1]
            name = _text(getattr(entry, "name", ""))
            directory = ""
            try:
                dir_index = int(getattr(entry, "dir_index", 0) or 0)
            except (TypeError, ValueError):
                dir_index = 0
            if dir_index > 0 and dir_index <= len(include_dirs):
                directory = _text(include_dirs[dir_index - 1])
            elif comp_dir:
                directory = comp_dir
            if directory and name and not Path(name).is_absolute():
                return str(Path(directory) / name)
            return name

        try:
            entries = lineprog.get_entries()
        except Exception:
            return
        for entry in entries:
            state = getattr(entry, "state", None)
            if state is None or getattr(state, "end_sequence", False):
                continue
            try:
                address = int(state.address)
                line = int(state.line or 0)
                file_index = int(state.file or 0)
            except (TypeError, ValueError):
                continue
            if line > 0:
                self._line_rows.append((address, file_name(file_index), line))

    @staticmethod
    def _kind(tag: str) -> str:
        return tag.removeprefix("DW_TAG_").removesuffix("_type")

    @staticmethod
    def _byte_size_attr(die) -> Optional[int]:
        attr = die.attributes.get("DW_AT_byte_size")
        if attr is None:
            return None
        try:
            return int(attr.value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _referenced_offset(die, attr_name: str = "DW_AT_type") -> Optional[int]:
        if attr_name not in die.attributes:
            return None
        try:
            target = die.get_DIE_from_attribute(attr_name)
        except Exception:
            return None
        return None if target is None else int(target.offset)

    def type_by_offset(self, offset: int) -> DwarfTypeInfo:
        selected = int(offset)
        cached = self._cache.get(selected)
        if cached is not None:
            return cached
        die = self._dies.get(selected)
        if die is None or die.tag not in _TYPE_TAGS:
            raise KeyError("DWARF type DIE 0x%X was not found." % selected)
        # Insert a minimal placeholder first so recursive/self-referential pointers
        # cannot recurse forever while member types are being described.
        placeholder = DwarfTypeInfo(selected, _attr_text(die, "DW_AT_name") or "<anonymous@0x%X>" % selected,
                                    self._kind(die.tag), self._byte_size_attr(die))
        self._cache[selected] = placeholder
        info = self._describe_die(die, placeholder)
        self._cache[selected] = info
        return info

    def _describe_die(self, die, placeholder: DwarfTypeInfo) -> DwarfTypeInfo:
        target_offset = self._referenced_offset(die)
        byte_size = self._byte_size_attr(die)
        if die.tag == "DW_TAG_pointer_type" and byte_size is None:
            byte_size = self.pointer_size
        if byte_size is None and target_offset is not None and die.tag in _WRAPPER_TAGS:
            try:
                byte_size = self.type_by_offset(target_offset).byte_size
            except Exception:
                byte_size = None

        members = []
        if die.tag in {"DW_TAG_structure_type", "DW_TAG_union_type"}:
            for child in die.iter_children():
                if child.tag != "DW_TAG_member":
                    continue
                name = _attr_text(child, "DW_AT_name") or "<anonymous>"
                location = child.attributes.get("DW_AT_data_member_location")
                offset = None
                if location is not None:
                    try:
                        if describe_form_class(location.form) == "constant":
                            offset = int(location.value)
                    except Exception:
                        offset = None
                member_type_offset = self._referenced_offset(child)
                type_name = ""
                member_size = None
                if member_type_offset is not None:
                    try:
                        member_type = self.type_by_offset(member_type_offset)
                        type_name = member_type.name
                        member_size = member_type.byte_size
                    except Exception:
                        pass
                members.append(DwarfMember(name, offset, member_type_offset, type_name, member_size))

        enum_values = []
        if die.tag == "DW_TAG_enumeration_type":
            for child in die.iter_children():
                if child.tag != "DW_TAG_enumerator":
                    continue
                name = _attr_text(child, "DW_AT_name")
                value = child.attributes.get("DW_AT_const_value")
                if name and value is not None:
                    try:
                        enum_values.append(DwarfEnumValue(name, int(value.value)))
                    except (TypeError, ValueError):
                        pass

        element_offset = target_offset if die.tag == "DW_TAG_array_type" else None
        element_count = None
        if die.tag == "DW_TAG_array_type":
            count = 1
            found = False
            for child in die.iter_children():
                if child.tag != "DW_TAG_subrange_type":
                    continue
                count_attr = child.attributes.get("DW_AT_count")
                upper_attr = child.attributes.get("DW_AT_upper_bound")
                try:
                    dimension = int(count_attr.value) if count_attr is not None else int(upper_attr.value) + 1
                except Exception:
                    dimension = None
                if dimension is not None and dimension >= 0:
                    count *= dimension
                    found = True
            element_count = count if found else None
            if byte_size is None and element_offset is not None and element_count is not None:
                try:
                    element_size = self.type_by_offset(element_offset).byte_size
                    if element_size is not None:
                        byte_size = element_size * element_count
                except Exception:
                    pass

        return DwarfTypeInfo(
            die_offset=int(die.offset),
            name=_attr_text(die, "DW_AT_name") or placeholder.name,
            kind=self._kind(die.tag),
            byte_size=byte_size,
            target_die_offset=target_offset,
            element_die_offset=element_offset,
            element_count=element_count,
            members=tuple(members),
            enum_values=tuple(enum_values),
        )

    def resolve_type(self, name: str) -> DwarfTypeInfo:
        selected = str(name).strip()
        offsets = self._named_types.get(selected, ())
        if not offsets:
            raise KeyError("DWARF type was not found: %s" % selected)
        # Prefer typedefs because firmware-facing names such as TCB_t usually alias
        # an anonymous or internal structure name.
        ordered = sorted(offsets, key=lambda off: 0 if self._dies[off].tag == "DW_TAG_typedef" else 1)
        return self.type_by_offset(ordered[0])

    def resolve_symbol_type(self, symbol: str) -> DwarfTypeInfo:
        selected = str(symbol).strip()
        offsets = self._variables.get(selected, ())
        if not offsets:
            raise KeyError("DWARF variable was not found: %s" % selected)
        for offset in offsets:
            target = self._referenced_offset(self._dies[offset])
            if target is not None:
                return self.type_by_offset(target)
        raise KeyError("DWARF variable has no type information: %s" % selected)

    def canonical(self, info: DwarfTypeInfo) -> DwarfTypeInfo:
        current = info
        seen = set()
        while current.kind in {"typedef", "const", "volatile", "restrict", "atomic"}:
            if current.die_offset in seen or current.target_die_offset is None:
                break
            seen.add(current.die_offset)
            current = self.type_by_offset(current.target_die_offset)
        return current

    def member(self, type_or_name, member_name: str) -> DwarfMember:
        info = self.resolve_type(type_or_name) if isinstance(type_or_name, str) else type_or_name
        current = self.canonical(info)
        if current.kind not in {"structure", "union"}:
            raise TypeError("DWARF type %s is not a structure/union." % info.name)
        for member in current.members:
            if member.name == member_name:
                if member.offset is None:
                    raise RuntimeError("DWARF member offset is not a constant: %s.%s" % (info.name, member_name))
                return member
        raise KeyError("DWARF member was not found: %s.%s" % (info.name, member_name))

    def member_type(self, type_or_name, member_name: str) -> DwarfTypeInfo:
        member = self.member(type_or_name, member_name)
        if member.type_die_offset is None:
            raise KeyError("DWARF member has no type: %s" % member_name)
        return self.type_by_offset(member.type_die_offset)

    def sizeof(self, type_or_name) -> int:
        info = self.resolve_type(type_or_name) if isinstance(type_or_name, str) else type_or_name
        if info.byte_size is None:
            canonical = self.canonical(info)
            if canonical.byte_size is None:
                raise RuntimeError("DWARF byte size is unavailable for %s." % info.name)
            return int(canonical.byte_size)
        return int(info.byte_size)

    def resolve_address(self, address: int) -> DwarfSourceLocation:
        selected = int(address)
        function = None
        for low, high, name in self._functions:
            if low <= selected < high:
                function = name
                break
        file = None
        line = None
        if self._line_addresses:
            index = bisect_right(self._line_addresses, selected) - 1
            if index >= 0:
                row_address, row_file, row_line = self._line_rows[index]
                # Avoid mapping wildly unrelated addresses to the last line entry.
                next_address = self._line_rows[index + 1][0] if index + 1 < len(self._line_rows) else None
                if next_address is None or selected < next_address or function is not None:
                    file = row_file or None
                    line = row_line
        return DwarfSourceLocation(selected, function, file, line)
