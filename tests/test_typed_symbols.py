from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from b300_core.typed_symbols import TypedSymbolCatalog


FIXTURES = Path(__file__).parent / "fixtures"


def _build_keil_fixture(directory: str) -> Path:
    armcc = shutil.which("armcc") or r"C:\Keil_v5\ARM\ARMCC\bin\armcc.exe"
    armlink = shutil.which("armlink") or r"C:\Keil_v5\ARM\ARMCC\bin\armlink.exe"
    if not Path(armcc).is_file() or not Path(armlink).is_file():
        raise unittest.SkipTest("Keil ARMCC fixture compiler is not installed")
    output = Path(directory) / "typed_variables.axf"
    objects = []
    for source_name in ("typed_variables.c", "typed_variables_other.c"):
        obj = Path(directory) / (Path(source_name).stem + ".o")
        subprocess.run(
            [armcc, "--c99", "--debug", "-O0", "-c", str(FIXTURES / source_name), "-o", str(obj)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        objects.append(obj)
    subprocess.run(
        [armlink, *(str(item) for item in objects), "--ro-base", "0x08010000",
         "--rw-base", "0x20000100", "--entry", "Reset_Handler", "--debug", "--no_remove",
         "--output", str(output)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return output


class TypedSymbolCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.image = _build_keil_fixture(cls.temp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def setUp(self) -> None:
        self.catalog = TypedSymbolCatalog(self.image)

    def _root(self, name: str):
        matches = tuple(item for item in self.catalog.roots(name, 0, 100) if item.name == name)
        self.assertEqual([item.name for item in matches], [name])
        return matches[0]

    def _child(self, parent, name: str):
        matches = [item for item in self.catalog.children(parent.node_id, 0, 100)
                   if item.name == name]
        self.assertEqual(len(matches), 1, name)
        return matches[0]

    def test_keil_dwarf_offsets_drive_nested_struct_union_and_array_addresses(self):
        root = self._root("g_machine")
        self.assertEqual((root.address, root.kind, root.byte_size),
                         (0x20000000, "structure", 28))

        position = self._child(root, "position")
        self.assertEqual((position.address, position.kind), (0x20000000, "structure"))
        self.assertEqual(self._child(position, "y").address, 0x20000002)

        rpm = self._child(root, "rpm")
        self.assertEqual((rpm.address, rpm.kind, rpm.byte_size), (0x20000004, "array", 4))
        self.assertEqual(self._child(rpm, "[1]").address, 0x20000006)

        sensor = self._child(root, "sensor")
        union_children = self.catalog.children(sensor.node_id, 0, 100)
        self.assertEqual([item.name for item in union_children], ["raw", "volts"])
        self.assertEqual({item.address for item in union_children}, {0x20000008})

    def test_scalar_type_enum_bitfield_and_pointer_come_from_dwarf(self):
        root = self._root("g_machine")
        x = self._child(self._child(root, "position"), "x")
        mode = self._child(root, "mode")
        flags = self._child(root, "flags")
        pointer = self._child(root, "next")

        self.assertEqual((x.value_type, x.address, x.byte_size), ("i16", 0x20000000, 2))
        self.assertEqual(mode.enum_values, ((0, "MODE_IDLE"), (3, "MODE_RUN")))
        # ARMCC DWARF2 stores this field in the uint32 allocation unit at +12;
        # the field itself starts at bit 8 because the compact enum occupies byte +12.
        self.assertEqual((flags.address, flags.bit_offset, flags.bit_size),
                         (0x2000000C, 8, 3))
        self.assertEqual((pointer.kind, pointer.value_type, pointer.has_children),
                         ("pointer", "u32", False))
        self.assertIn("Pointer", pointer.reason)

    def test_multidimensional_array_is_lazy_and_paged(self):
        matrix = self._child(self._root("g_machine"), "matrix")
        outer = self.catalog.children(matrix.node_id, 0, 1)
        self.assertEqual([item.name for item in outer], ["[0]"])
        self.assertTrue(outer[0].has_children)
        self.assertEqual(
            [(item.name, item.address) for item in self.catalog.children(outer[0].node_id, 0, 100)],
            [("[0]", 0x2000000F), ("[1]", 0x20000010), ("[2]", 0x20000011)],
        )
        self.assertEqual([item.name for item in self.catalog.children(matrix.node_id, 1, 1)], ["[1]"])

    def test_static_duplicate_names_remain_distinct_and_node_ids_bind_file_hash(self):
        roots = self.catalog.roots("duplicate_static", 0, 100)
        self.assertEqual(len(roots), 2)
        self.assertEqual(len({item.node_id for item in roots}), 2)
        self.assertTrue(all(self.catalog.fingerprint in item.node_id for item in roots))
        self.assertEqual(len({item.source_file for item in roots}), 2)

    def test_real_main_v2_keil_axf_exposes_xagvinfor_member_offsets_when_present(self):
        path = Path(r"C:\Users\Admin\Documents\STM32\B300-Main-Custom\Objects\F407\Main_V2_F407.axf")
        if not path.is_file():
            self.skipTest("Main_V2_F407.axf is not available")
        catalog = TypedSymbolCatalog(path)
        root = next(item for item in catalog.roots("xAgvInfor", 0, 10)
                    if item.name == "xAgvInfor")
        children = catalog.children(root.node_id, 0, 100)
        by_name = {item.name: item for item in children}
        self.assertEqual(root.address, 0x2000D198)
        self.assertEqual(by_name["direction"].address, 0x2000D19A)
        self.assertEqual(by_name["RFID"].address, 0x2000D1A0)
        self.assertEqual(by_name["distance"].address, 0x2000D1F0)


if __name__ == "__main__":
    unittest.main()
