from __future__ import annotations

import unittest
from types import SimpleNamespace

from b300_core.fault import CortexMFaultDecoder, FaultService
from b300_core.peripheral import PeripheralService, SvdLoader
from b300_core.target import STM32F407ZE, default_registry


class _Block:
    def __init__(self, address, data):
        self.address = address
        self.data = bytes(data)


class _Memory:
    target_state = "halted"

    def __init__(self, reads):
        self.reads = dict(reads)
        self.calls = []

    def read(self, address, length):
        self.calls.append((address, length))
        data = self.reads[(address, length)]
        return _Block(address, data)


class _Workspace:
    def __init__(self, registers):
        self._registers = tuple(SimpleNamespace(name=name, value=value) for name, value in registers)

    def registers(self):
        return self._registers


class TargetDescriptionTests(unittest.TestCase):
    def test_f407_profile_matches_b300_flash_and_safe_regions(self):
        target = default_registry().get("stm32f407ze")
        self.assertEqual(target.flash_bytes, 512 * 1024)
        self.assertEqual(target.classify_address(0x08000000).name, "FLASH")
        self.assertEqual(target.classify_address(0x0807FFFF).name, "FLASH")
        self.assertIsNone(target.classify_address(0x08080000))
        self.assertEqual(target.classify_address(0x20000000).kind, "ram")
        self.assertEqual(target.classify_address(0xE000ED28).kind, "system")


class SvdPeripheralTests(unittest.TestCase):
    SVD = """<?xml version='1.0'?>
<device>
  <name>TEST-F407</name><size>32</size>
  <peripherals>
    <peripheral>
      <name>USART1</name><baseAddress>0x40011000</baseAddress>
      <registers>
        <register>
          <name>SR</name><addressOffset>0x00</addressOffset><size>32</size>
          <fields>
            <field><name>RXNE</name><bitOffset>5</bitOffset><bitWidth>1</bitWidth></field>
            <field><name>TXE</name><bitRange>[7:7]</bitRange></field>
          </fields>
        </register>
      </registers>
    </peripheral>
  </peripherals>
</device>"""

    def test_loader_and_service_are_lazy_read_only_and_decode_fields(self):
        device = SvdLoader.loads(self.SVD)
        memory = _Memory({(0x40011000, 4): (0xA0).to_bytes(4, "little")})
        service = PeripheralService(memory, STM32F407ZE, device)
        self.assertEqual(memory.calls, [])
        snapshot = service.inspect_register("USART1", "SR")
        self.assertEqual(snapshot.raw_value, 0xA0)
        self.assertEqual({field.name: field.value for field in snapshot.fields}, {"RXNE": 1, "TXE": 1})
        self.assertEqual(memory.calls, [(0x40011000, 4)])
        self.assertFalse(hasattr(service, "write_register"))

    def test_svd_address_outside_target_peripherals_fails_closed(self):
        device = SvdLoader.loads(self.SVD.replace("0x40011000", "0x20000000"))
        service = PeripheralService(_Memory({}), STM32F407ZE, device)
        with self.assertRaises(ValueError):
            service.inspect_register("USART1", "SR")


class CortexFaultTests(unittest.TestCase):
    def test_decoder_reports_precise_bus_fault_and_bfar(self):
        cfsr = (1 << 9) | (1 << 15)
        flags = CortexMFaultDecoder.decode_flags(cfsr, 1 << 30)
        names = {flag.name for flag in flags}
        self.assertTrue({"PRECISERR", "BFARVALID", "FORCED"}.issubset(names))
        self.assertEqual(CortexMFaultDecoder.fault_address(cfsr, 0, 0x20020004), (0x20020004, "BFAR"))

    def test_fault_service_reconstructs_basic_psp_exception_frame_without_reset(self):
        shcsr = 0
        cfsr = (1 << 9) | (1 << 15)
        hfsr = 1 << 30
        dfsr = 0
        mmfar = 0
        bfar = 0x20020004
        afsr = 0
        scb = b"".join(value.to_bytes(4, "little") for value in (
            shcsr, cfsr, hfsr, dfsr, mmfar, bfar, afsr,
        ))
        frame_words = (1, 2, 3, 4, 12, 0x08001235, 0x08012345, 0x21000000)
        frame = b"".join(value.to_bytes(4, "little") for value in frame_words)
        memory = _Memory({
            (FaultService.SCB_SHCSR, 28): scb,
            (0x20001000, 32): frame,
        })
        workspace = _Workspace((
            ("lr", "0xFFFFFFFD"),
            ("msp", "0x20002000"),
            ("psp", "0x20001000"),
        ))
        service = FaultService(workspace, memory, STM32F407ZE)
        result = service.capture()
        self.assertEqual(result.exception_frame.pc, 0x08012345)
        self.assertTrue(result.exception_frame.used_psp)
        self.assertEqual(result.fault_address, 0x20020004)
        self.assertIsNone(service.classify_fault_address(result))
        self.assertEqual(memory.calls, [(FaultService.SCB_SHCSR, 28), (0x20001000, 32)])


if __name__ == "__main__":
    unittest.main()
