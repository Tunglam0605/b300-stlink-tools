# Hardware Acceptance - B300 STM32F407 - 2026-08-28

## Mục tiêu

Xác nhận trên phần cứng thật rằng `b300-stlink-tools` giữ đúng ranh giới flash B300,
không phá Bootloader trong luồng Application, có thể Factory-provision trusted
Bootloader, giữ nguyên RDP và khôi phục WRP Sector 0-2 sau transaction.

## Thiết bị thử nghiệm

- MCU: STM32F407, OpenOCD device ID `0x101F6413` (family ID `0x413`).
- Flash: 512 KiB.
- ST-Link: `STLINK V2J35S7`, VID:PID `0483:3748`.
- Target voltage quan sát: khoảng `3.08 V`.
- OpenOCD: xPack Open On-Chip Debugger `0.12.0+dev-02228-ge5888bda3-dirty`.
- GDB: `arm-none-eabi-gdb` từ STM32CubeIDE 2.2.0.

Lưu ý: ST-Link V2 dùng trong ca thử này không expose một serial USB ổn định qua
Windows PnP nên Factory hardware acceptance gọi core service với single-probe
auto-select. GUI/CLI destructive Factory policy vẫn yêu cầu explicit probe serial
khi môi trường discovery cung cấp được serial hợp lệ.

## Flash map được nghiệm thu

| Sector | Address | Vai trò | Trạng thái sau nghiệm thu |
|---|---|---|---|
| S0 | `0x08000000..0x08003FFF` | Bootloader | WRP protected |
| S1 | `0x08004000..0x08007FFF` | Bootloader | WRP protected |
| S2 | `0x08008000..0x0800BFFF` | Bootloader | WRP protected |
| S3 | `0x0800C000..0x0800FFFF` | OTA metadata | not protected / ERASED |
| S4 | `0x08010000..0x0801FFFF` | Application | not protected |
| S5 | `0x08020000..0x0803FFFF` | Application | not protected |
| S6 | `0x08040000..0x0805FFFF` | Application | not protected |
| S7 | `0x08060000..0x0807FFFF` | Application | not protected |

## Baseline trước destructive validation

- RDP: `0xAA` / Level 0.
- S0-S7 ban đầu đều `not protected`.
- Bootloader vector ban đầu: MSP `0x20001910`, Reset `0x080002D5`.
- Application vector ban đầu: MSP `0x200185C8`, Reset `0x08010361`.
- S3 metadata: `ERASED` (`0xFF`).
- Backup S0-S2 trước Factory được lưu local ngoài Git.
- SHA-256 backup 48 KiB S0-S2 trước Factory:
  `1430DEC44204C5E8E8DF2829E0C930BC8D138A1D97C1397556A60E1D5C47D6B2`.
- Bootloader đang có trên board trước Factory khác trusted artifact ở nhiều byte.

## Validation 1 - WRP provisioning

Đã chạy có chủ đích:

```text
flash protect 0 0 2 on
reset halt
flash info 0
```

Kết quả:

- S0-S2: `protected`.
- S3-S7: `not protected`.
- `FLASH_OPTCR = 0x0FF8AAED`.
- RDP vẫn `0xAA` / Level 0.
- Bootloader và Application vector không đổi.
- Sau `reset run`, service inspection vẫn đọc đúng S0-S2 protected.

## Validation 2 - Normal Application provisioning

Artifact: `firmware/application/Objects/Main_B300.hex`.

- start: `0x08010000`
- end: `0x0802FBD7`
- SHA-256: `6A8E2A673683FE83E41939253EE1FCF8DFEF8C31373E1EC7F232C0E224BB77CB`
- vector: MSP `0x20019AD8`, Reset `0x08010361`.

Dry-run trước hardware write chỉ tạo destructive command:

```text
flash erase_sector 0 3 7
program {Main_B300.hex} verify
reset run
```

Không có `flash protect`, mass erase, RDP mutation hoặc Sector 0-2 erase.

Hardware result:

- OpenOCD: `erased sectors 3 through 7`.
- Program: PASS.
- Verify: `** Verified OK **`.
- Post-verify PC: `0x08026686` trong Application range.
- BKP1R `0x40002854`: `0x00000000`.
- Application vector sau nạp: MSP `0x20019AD8`, Reset `0x08010361`.
- Bootloader vector vẫn: MSP `0x20001910`, Reset `0x080002D5`.
- WRP S0-S2 vẫn protected.
- RDP vẫn Level 0.
- S3 metadata vẫn ERASED.

### Cold power-cycle sau Application provisioning

Board được tắt nguồn khoảng 5 giây và cấp lại.

- WRP S0-S2 vẫn protected.
- RDP vẫn Level 0.
- Application vector vẫn đúng image mới.
- PC quan sát: `0x08026682` trong Application range.
- BKP1R: `0x00000000`.
- CPU được resume sau read-only inspection.

**Normal Application Hardware Validation: PASS.**

## Validation 3 - OpenOCD + GDB/MI hardware debug

OpenOCD lifecycle thật: `START -> READY -> STOPPED`.

GDB/MI thật qua `127.0.0.1:3333`:

```text
1-target-select remote 127.0.0.1:3333
1^connected

-exec-interrupt
2^done

-exec-continue
3^running

-target-disconnect
4^done
```

Token correlation và verified MI result hoạt động với target thật; CPU được continue
trước khi disconnect.

**Debug Hardware Validation: PASS.**

## Validation 4 - Factory trusted Bootloader provisioning

Trusted artifact:
`resources/firmware/b300_bootloader_f407ze_com3_v00050000.hex`.

- board: `B300_F407ZE`
- firmware version: `0x00050000`
- protocol version: `0x00030000`
- source commit: `19b42d8ec30e700a9c6bb7772e444fa538adca03`
- raw HEX SHA-256:
  `C0FC6083EEBA39ED5F2AF40D97ECA90FEAE15E4B1D32B150C1504869D18D9398`

Factory transaction thực tế:

```text
WRP OFF S0-S2
reset halt / reload Option Bytes
verify S0-S2 not protected
flash erase_sector 0 0 2
program trusted Bootloader verify
WRP ON S0-S2
reset halt / reload Option Bytes
verify S0-S2 protected
reset run
post-verify WRP
```

Kết quả:

- WRP OFF: PASS và được re-inspect xác nhận.
- Erase: chỉ S0-S2.
- Program: PASS.
- Verify: `** Verified OK **`.
- WRP ON: PASS và được re-inspect xác nhận.
- S3-S7 không bị erase/program trong Factory transaction.
- RDP không thay đổi.
- Application vẫn chạy sau Factory; PC quan sát `0x08023E60`.
- BKP1R vẫn `0x00000000`.
- S3 metadata vẫn ERASED.

### Bit-for-bit verification

Trusted HEX được reconstruct thành toàn bộ 48 KiB S0-S2; vùng không có data record
được điền `0xFF`.

```text
Expected reconstructed S0-S2 SHA-256:
0775682DAC7205F9ABF158184B695BA303304CCFD76E6A850FAC51A422E4AA39

Board dump S0-S2 after Factory SHA-256:
0775682DAC7205F9ABF158184B695BA303304CCFD76E6A850FAC51A422E4AA39

FULL_MATCH = True
```

### Cold power-cycle sau Factory provisioning

Board được tắt nguồn và cấp lại.

- S0-S2 vẫn WRP protected.
- S3-S7 vẫn not protected.
- `FLASH_OPTCR = 0x0FF8AAED`, RDP Level 0.
- Trusted Bootloader dump sau power-cycle vẫn có SHA-256
  `0775682DAC7205F9ABF158184B695BA303304CCFD76E6A850FAC51A422E4AA39`.
- `FULL_MATCH = True` với trusted reconstructed S0-S2.
- Bootloader vector: MSP `0x20001910`, Reset `0x080002D5`.
- Application vector: MSP `0x20019AD8`, Reset `0x08010361`.
- PC quan sát: `0x08023E60` trong Application range.
- BKP1R: `0x00000000`.
- Metadata: ERASED.
- CPU được resume sau inspection.

**Factory Bootloader Hardware Validation: PASS.**

## Kết luận nghiệm thu

| Gate | Result |
|---|---|
| STM32F407 / 512 KiB target detection | PASS |
| RDP Level 0 preservation | PASS |
| WRP S0-S2 enable + persistence | PASS |
| Normal erase S3-S7 only | PASS |
| Application program + verify | PASS |
| Bootloader preservation during Application flow | PASS |
| Application cold boot | PASS |
| OpenOCD debug lifecycle | PASS |
| GDB/MI real hardware | PASS |
| Factory WRP OFF + verify | PASS |
| Factory erase/program S0-S2 only | PASS |
| Trusted Bootloader verify | PASS |
| Factory WRP restoration | PASS |
| Bit-for-bit trusted Bootloader | PASS |
| Factory cold power-cycle persistence | PASS |
| Metadata preservation during Factory | PASS |
| Application preservation during Factory | PASS |

Hardware evidence files (`*.bin`, live logs) được giữ local trong `hardware-validation/`
và bị loại khỏi Git để tránh commit firmware dump từ board thử nghiệm.
