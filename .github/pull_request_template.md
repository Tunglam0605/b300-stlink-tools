## Mục tiêu

Mô tả ngắn vấn đề và kết quả của thay đổi.

## Phạm vi thay đổi

- [ ] CLI/tooling
- [ ] Flash/provisioning
- [ ] Debug
- [ ] Packaging/installer
- [ ] Tài liệu/Agent Skill

## Xác thực

Ghi rõ lệnh đã chạy và kết quả. Nếu có kiểm thử phần cứng, nêu board, probe và
phạm vi thao tác.

```text
python3 -m unittest discover -s tests -q
```

## Checklist an toàn

- [ ] Không dùng mass/chip erase.
- [ ] Không erase/program Sector 0–2 Bootloader.
- [ ] Flash thật chỉ chạy sau khi xác nhận đúng board, HEX và probe.
- [ ] Không tự retry khi erase/program/verify lỗi.
- [ ] Debug remote không mở Telnet/TCL.
- [ ] Không commit firmware, binary, credential, log nhạy cảm hoặc release archive.
- [ ] Tài liệu và Agent Skill đã được cập nhật nếu hành vi vận hành thay đổi.
