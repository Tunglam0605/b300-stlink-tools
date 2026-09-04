"""Small read-only CMSIS-SVD loader with bounded input size."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from .svd_model import SvdDevice, SvdField, SvdPeripheral, SvdRegister


class SvdLoader:
    MAX_SVD_BYTES = 16 * 1024 * 1024

    @staticmethod
    def _text(node, name: str, default: str = "") -> str:
        child = node.find(name)
        if child is None or child.text is None:
            return default
        return child.text.strip()

    @classmethod
    def _number(cls, node, name: str, default: Optional[int] = None) -> Optional[int]:
        text = cls._text(node, name)
        if not text:
            return default
        text = text.replace("#", "0b")
        return int(text, 0)

    @classmethod
    def _field(cls, node) -> SvdField:
        offset = cls._number(node, "bitOffset")
        width = cls._number(node, "bitWidth")
        if offset is None or width is None:
            lsb = cls._number(node, "lsb")
            msb = cls._number(node, "msb")
            if lsb is not None and msb is not None:
                offset, width = lsb, msb - lsb + 1
        if offset is None or width is None:
            bit_range = cls._text(node, "bitRange")
            if bit_range.startswith("[") and bit_range.endswith("]") and ":" in bit_range:
                high, low = bit_range[1:-1].split(":", 1)
                msb, lsb = int(high, 0), int(low, 0)
                offset, width = lsb, msb - lsb + 1
        if offset is None or width is None or offset < 0 or not 1 <= width <= 64:
            raise ValueError("SVD field has invalid bit range.")
        return SvdField(
            name=cls._text(node, "name"),
            bit_offset=offset,
            bit_width=width,
            description=cls._text(node, "description"),
        )

    @classmethod
    def _register(cls, node, default_size: int) -> SvdRegister:
        size = cls._number(node, "size", default_size) or default_size
        if size not in (8, 16, 32, 64):
            raise ValueError("Unsupported SVD register size: %s" % size)
        fields_node = node.find("fields")
        fields = () if fields_node is None else tuple(cls._field(field) for field in fields_node.findall("field"))
        return SvdRegister(
            name=cls._text(node, "name"),
            address_offset=cls._number(node, "addressOffset", 0) or 0,
            size_bits=size,
            access=cls._text(node, "access") or None,
            reset_value=cls._number(node, "resetValue"),
            fields=fields,
            description=cls._text(node, "description"),
        )

    @classmethod
    def loads(cls, xml_text: str) -> SvdDevice:
        data = xml_text.encode("utf-8")
        if len(data) > cls.MAX_SVD_BYTES:
            raise ValueError("SVD exceeds the %d-byte safety limit." % cls.MAX_SVD_BYTES)
        root = ET.fromstring(data)
        default_size = cls._number(root, "size", 32) or 32
        peripherals_node = root.find("peripherals")
        if peripherals_node is None:
            raise ValueError("SVD contains no peripherals section.")
        peripherals = []
        for node in peripherals_node.findall("peripheral"):
            registers_node = node.find("registers")
            registers = () if registers_node is None else tuple(
                cls._register(register, default_size) for register in registers_node.findall("register")
            )
            peripherals.append(SvdPeripheral(
                name=cls._text(node, "name"),
                base_address=cls._number(node, "baseAddress", 0) or 0,
                registers=registers,
                description=cls._text(node, "description"),
            ))
        return SvdDevice(
            name=cls._text(root, "name", "CMSIS-SVD"),
            peripherals=tuple(peripherals),
            description=cls._text(root, "description"),
        )

    @classmethod
    def load(cls, path: Path) -> SvdDevice:
        selected = Path(path).expanduser().resolve()
        if selected.suffix.lower() != ".svd":
            raise ValueError("Peripheral description must be a .svd file.")
        if not selected.is_file():
            raise ValueError("SVD file does not exist: %s" % selected)
        if selected.stat().st_size > cls.MAX_SVD_BYTES:
            raise ValueError("SVD exceeds the %d-byte safety limit." % cls.MAX_SVD_BYTES)
        return cls.loads(selected.read_text(encoding="utf-8"))
