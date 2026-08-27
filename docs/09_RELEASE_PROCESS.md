# Quy trình phát hành B300 ST-Link Tools

GitHub Release là nguồn phân phối chính thức. Người dùng chỉ tải artifact từ
Release; không cần clone source repository.

## Chuẩn bị một phiên bản

1. Chuyển về `main`, lấy thay đổi mới nhất và bảo đảm CI hiện tại xanh.

   ```bash
   git checkout main
   git pull --ff-only origin main
   ```

2. Chọn phiên bản Semantic Version mới và cập nhật từ nguồn duy nhất.

   ```bash
   python -m scripts.release.bump_version 0.3.1
   ```

3. Cập nhật mục `## [0.3.1] - YYYY-MM-DD` trong `CHANGELOG.md`. Mục này là
   release notes chính thức, không viết lại thủ công trên GitHub.

4. Kiểm tra trước khi commit.

   ```bash
   python -m scripts.release.validate_version
   python -m unittest discover -s tests -q
   ```

5. Commit và push source. Chờ workflow CI xanh trên Windows x64, Ubuntu x64
   và Ubuntu ARM64.

   ```bash
   git add b300_version.py CHANGELOG.md
   git commit -m "release: prepare v0.3.1"
   git push origin main
   ```

## Tạo Release chính thức

Chỉ sau khi CI của commit đã xanh, tạo đúng một tag trỏ vào commit đó.

```bash
git tag v0.3.1
git push origin v0.3.1
```

Workflow `Publish B300 ST-Link Tools release` tự làm các việc sau: kiểm tra
tag/source version, build Windows/Ubuntu x64/Ubuntu ARM64, smoke test, tạo
checksum, ký manifest bằng Minisign, tạo draft, kiểm tra đủ asset rồi mới
publish và đánh dấu `Latest`.

Không tạo lại cùng một tag. Nếu Release có lỗi trước khi publish, sửa source,
tăng patch version, chạy CI lại rồi phát hành một tag mới (ví dụ `v0.3.2`).

## Xác minh sau phát hành

1. Mở trang Release và kiểm tra đủ GUI/CLI cho ba nền tảng, `SHA256SUMS.txt`,
   `release-manifest.json`, `release-manifest.json.minisig`, `latest.json` và
   `latest.json.minisig`.
2. Kiểm tra direct link stable, không thay README theo từng phiên bản:

   ```text
   https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-GUI-Windows-x64.exe
   https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/latest.json
   ```

3. Ghi kết quả software gate vào `docs/08_RELEASE_ACCEPTANCE.md`. Không tự
   coi software release là nghiệm thu phần cứng: flash/board/ST-Link phải được
   xác nhận riêng theo checklist an toàn.
