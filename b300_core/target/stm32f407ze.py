"""B300 production target profile for STM32F407ZET6."""

from .target_description import MemoryRegion, TargetCapabilities, TargetDescription


STM32F407ZE = TargetDescription(
    key="stm32f407ze",
    vendor="STMicroelectronics",
    family="STM32F4",
    part="STM32F407ZE",
    core="Cortex-M4F",
    flash_bytes=512 * 1024,
    breakpoint_count=6,
    watchpoint_count=4,
    memory_regions=(
        MemoryRegion("CCM SRAM", 0x10000000, 64 * 1024, "ram", True),
        MemoryRegion("SRAM", 0x20000000, 128 * 1024, "ram", True),
        MemoryRegion("FLASH", 0x08000000, 512 * 1024, "flash", False),
        MemoryRegion("PERIPHERALS", 0x40000000, 0x20000000, "peripheral", True),
        MemoryRegion("CORTEX PPB", 0xE0000000, 0x00100000, "system", True),
    ),
    capabilities=TargetCapabilities(
        dwt=True,
        fpu=True,
        nvic=True,
        scb=True,
        svd=True,
        swo=True,
        itm=True,
    ),
    # Deliberately a hint only: vendor SVD redistribution/licensing must be
    # approved before any file is bundled in installers.
    svd_hint="STM32F407.svd",
)
