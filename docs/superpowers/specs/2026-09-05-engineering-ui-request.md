MỤC TIÊU
========

Refactor frontend B300 ST-Link Tools theo bộ ảnh GUI reference tôi cung cấp,
để giao diện đạt mức:

- hiện đại
- chuyên nghiệp
- chuẩn công cụ kỹ sư
- ít chữ thừa
- thông tin dày nhưng dễ quét
- chức năng rõ owner
- tránh duplicate
- tránh bắt người dùng chọn lại Project / Gateway / Connection ở nhiều nơi
- không biến B300 thành một IDE thứ hai

Repo canonical:

C:\Users\Admin\Documents\STM32\b300-stlink-tools

Current stable baseline:
v0.19.1

Trước khi sửa:
1. đọc AGENTS.md
2. đọc kiến trúc GUI hiện tại
3. đọc các shared store/session hiện có
4. đọc implementation PROGRAM preflight v0.19.1
5. kiểm tra git status / branch / HEAD
6. tạo branch riêng cho GUI refactor

Gợi ý branch:

refactor/v0.20-engineering-ui


==================================================
REFERENCE DESIGN
==================================================

Tôi sẽ gửi kèm 5 ảnh reference:

1. SETTINGS
2. PROGRAM
3. MONITOR
4. DEBUG
5. DEVICE

Các ảnh này là:

VISUAL / UX REFERENCE ONLY.

Dùng chúng để tham khảo:

- bố cục
- tỷ lệ panel
- spacing
- typography
- card
- icon
- màu
- visual hierarchy
- cách sắp xếp thông tin
- phong cách dark engineering

TUYỆT ĐỐI KHÔNG copy mù:

- địa chỉ flash trong ảnh
- sector number
- memory size
- WRP/RDP value
- port
- version
- device ID
- firmware version
- metadata layout
- fake Gateway IP
- fake project paths
- fake OpenOCD/GDB status

Ảnh do AI mockup sinh ra nên có thể có dữ liệu kỹ thuật sai.

Mọi dữ liệu kỹ thuật phải lấy từ:

existing B300 core
existing constants
target inspection
project profile
connection profile
flash plan
actual runtime state

CODE / CORE là source of truth.
Ảnh chỉ là style reference.


==================================================
KIẾN TRÚC NAVIGATION CUỐI CÙNG
==================================================

Sidebar chỉ gồm:

PROGRAM
MONITOR
DEBUG VS CODE
DEVICE
SETTINGS

Không dùng:

WORKBENCH

Không gộp MONITOR + DEBUG vào một panel 50/50 nữa.

Lý do:

MONITOR cần diện tích lớn để xem:

- variables
- realtime values
- trend
- recent samples
- monitor state

DEBUG hiện tại chủ yếu là:

- prepare debug environment
- OpenOCD/GDB
- generate config
- open VS Code/Cortex-Debug

Do đó tách:

MONITOR
DEBUG VS CODE

nhưng chúng dùng chung:

Project
Connection
Probe
Target
Session


==================================================
NGUYÊN TẮC SHARED CONTEXT
==================================================

Project và Connection KHÔNG được chọn lại độc lập trên từng page.

Tạo/hoàn thiện một global App Context:

AppContext
├── selected_project
├── selected_connection
├── selected_probe
├── target_info
├── gateway_session
└── hardware_session state

Các page chỉ observe/render context.

Global context strip nên dùng chung:

Project      [ B300 Main ▼ ]
Connection   [ Gateway Robot 01 ▼ ]  ● Connected
Probe        [ STM32 STLink ▼ ]
Target       STM32F407ZET6

[Quản lý kết nối]
[Quản lý dự án]

Không tạo 5 implementation riêng trên 5 page.

Hãy tạo reusable widget/component:

SharedContextBar

và đặt nó trên:

PROGRAM
MONITOR
DEBUG VS CODE
DEVICE

SETTINGS không nhất thiết cần thanh này.


==================================================
CONNECTION MODEL
==================================================

Loại bỏ requirement người dùng phải chọn:

[Local]
[Gateway]
[Client]

ở MONITOR hoặc DEBUG.

Đây là implementation detail.

Thay bằng:

Connection:
[ Local ST-Link ▼ ]

hoặc:

Connection:
[ Gateway Robot 01 ▼ ]

Profile tự chứa loại kết nối.

Ví dụ conceptual:

ConnectionProfile
{
  id
  name
  type: local | ssh_gateway
  host
  user
  port
}

Nếu Local:

B300 tự dùng local ST-Link/OpenOCD.

Nếu Gateway:

B300 tự hiểu:
SSH
→ authentication
→ shared session
→ tunnel/remote OpenOCD
→ Monitor/Debug consumer

Không bắt operator chọn thêm “Client mode”.


==================================================
SSH / GATEWAY UX
==================================================

Gateway/SSH được quản lý duy nhất tại:

SETTINGS
→ Quản lý kết nối

Mô hình giống MobaXterm:

Gateway Robot 01
aubot@192.168.1.xxx:22

Gateway Company
user@10.x.x.x:22

Actions:

Add
Edit
Delete
Connect
Disconnect
Set Default
Test

Password:

- không lưu persistent
- chỉ RAM/session
- đóng B300 → clear
- trong cùng một lần mở B300:
  MONITOR ↔ DEBUG VS CODE
  không nhập lại password

Không đặt Host/User/Port/password field trực tiếp trên:

MONITOR
DEBUG VS CODE
PROGRAM


==================================================
PROJECT MODEL
==================================================

Project cũng chỉ quản lý một nơi:

SETTINGS
→ Quản lý dự án

Project profile conceptual:

ProjectProfile
{
  id
  name
  workspace
  elf_axf
  application_hex
  target_family
}

Ví dụ:

B300 Main
├─ Workspace
├─ Main_V2_F407.axf
├─ Main_V2_F407.hex
└─ STM32F407ZET6

Sau đó:

PROGRAM
→ lấy default HEX từ Project

MONITOR
→ lấy symbols ELF/AXF từ Project

DEBUG VS CODE
→ lấy Workspace + ELF/AXF

Không bắt user browse lại file ở cả ba nơi.

Vẫn phải hỗ trợ override nếu cần,
nhưng đưa vào secondary/advanced action,
không phải workflow mặc định.


==================================================
VISUAL SYSTEM
==================================================

Giữ style giống reference:

Dark engineering UI.

Palette direction:

background:
dark navy / graphite

card:
dark blue-gray

primary accent:
cyan / electric blue

success:
green

warning:
amber

failure:
red

Quy tắc:

RED chỉ dùng khi:
CHECK ĐÃ CHẠY và thật sự FAILED.

Không dùng đỏ cho:

NOT_CHECKED
PENDING
NOT_CONNECTED_YET

Các trạng thái đó dùng:

neutral
gray
blue info

Spacing:

- consistent 8px grid hoặc tương đương
- cùng radius
- cùng padding
- cùng header height
- cùng card border
- cùng button height

Không để mỗi page một style.

Tạo design tokens / shared stylesheet thay vì hardcode từng widget.

Nếu hệ thống hiện tại dùng QSS:
hãy gom token/style reusable.

Ví dụ:

spacing
radius
font size
card border
primary
success
warning
danger
muted
background


==================================================
TOP APPLICATION HEADER
==================================================

Header chung giống reference:

B300 ST-Link Tools
STM32 • Program • Monitor • Debug VS Code • Device

Compact status:

ST-Link
● Connected

Connection
Gateway Robot 01

Target MCU
STM32F407ZET6

Quick actions chỉ nếu thật sự cần:

Mở dự án
Lịch sử
Cài đặt
Trợ giúp

Không duplicate:

Settings button nếu sidebar SETTINGS đã rõ,
trừ khi top icon là shortcut nhẹ.

Ưu tiên đơn giản.


==================================================
PROGRAM PAGE
==================================================

Visual layout bám ảnh PROGRAM reference.

PROGRAM chỉ owner:

- Application programming
- Factory Bootloader controlled workflow
- flash plan
- programming diagnostics

Workflow mặc định:

Project đã chọn
↓
firmware tự lấy từ Project
↓
NẠP APPLICATION
↓
automatic read-only preflight
↓
PASS
↓
canonical flash transaction

PROGRAM KHÔNG yêu cầu user sang DEVICE trước.

Preserve v0.19.1 behavior:

NẠP APPLICATION
→ auto preflight:

1. detect actual target
2. read flash size
3. WRP verify
4. RDP verify
5. validate HEX
6. generate safe flash plan
7. confirm
8. canonical flash transaction

Layout ưu tiên:

A. Status summary

Firmware
Probe/Target
Preflight
Protection

B. Firmware card

filename
path
address span
size
CRC
reset vector
SHA256

Primary visible data only.

Detailed hashes/info can collapse.

C. Target & Safety Preflight

Không cần nút duplicate “Inspect Target”.

Có thể show:

Target expected
Target detected
Flash
WRP
RDP
Metadata state

D. Flash plan

visual segmented flash memory bar.

BUT:

memory regions MUST come from actual B300 model.

Do not copy fake sector map from image.

E. Primary action:

[NẠP APPLICATION]

Secondary:

[Dry Run]
[Xem chi tiết]

Factory Bootloader phải nằm:

Advanced / Controlled factory workflow

không đặt ngang hàng quá nổi với Application flash
nếu dễ gây thao tác nhầm.

F. Log

collapsed/compact by default nếu cần.


==================================================
MONITOR PAGE
==================================================

MONITOR phải là page có diện tích làm việc lớn nhất.

Không đưa VS Code Debug panel vào đây.

Layout:

MONITOR

SharedContextBar

------------------------------------------------

Live Monitor Toolbar

Refresh:
0.1 / 0.2 / 0.5 / 1 / 2 / 5 s

Search/filter

[Start Monitor]
[Stop]

------------------------------------------------

Live Variables TABLE lớn

Columns:

Variable
Address
Current Value
Type
Status

Optional:
selected checkbox

Rows phải cao vừa đủ để đọc.

Không nhồi quá nhiều icon.

------------------------------------------------

Trend area

Cho chart đủ lớn.

Có thể:

3 charts ngang

hoặc:

1 chart lớn + signal selector

Ưu tiên readability hơn dashboard đẹp.

------------------------------------------------

Recent Samples

------------------------------------------------

Monitor Log

Right sidebar chỉ khoảng 20–25% width:

Probe
Target
Connection
Project
Monitor session state
Last sample
Health

Main monitor area khoảng 75–80%.

MONITOR tuyệt đối giữ:

zero-halt
non-intrusive
không halt CPU
không làm robot realtime bị ảnh hưởng.

Không thay đổi backend polling safety chỉ để làm UI.


==================================================
DEBUG VS CODE PAGE
==================================================

Tên page:

DEBUG VS CODE

Không dùng generic “DEBUG” nếu B300 không thực hiện full IDE debug UI.

Mục tiêu page:

“B300 chuẩn bị môi trường và mở VS Code.”

Không recreate:

breakpoints
watch
call stack
register window
step over
step into
source editor

VS Code/Cortex-Debug sở hữu những thứ đó.

Page structure:

SharedContextBar

Debug Environment

OpenOCD
GDB
Cortex-Debug
Workspace
Symbols

Status:

Ready
Missing
Error

Primary CTA:

[MỞ DEBUG TRONG VS CODE]

Secondary:

[Tạo/Cập nhật launch.json]
[Kiểm tra kết nối]
[Mở workspace]
[Xem log]

Quick guide:

1. Chọn Project & Connection
2. Mở VS Code
3. Nhấn F5

Diagnostics:

ST-Link access
Gateway session
OpenOCD
GDB
SSH tunnel
VS Code
Cortex-Debug
Symbols

Không có:

Local / Gateway / Client selector.

Connection profile tự quyết định.


==================================================
DEVICE PAGE
==================================================

DEVICE là canonical owner của:

Target Inspection

Read-only diagnostics.

Layout bám ảnh reference:

Top summary:

ST-Link
Target MCU
Flash
Protection
VTarget

Detailed Target Info:

Device ID
Revision
Flash
SRAM
Option Bytes
RDP
WRP
Vector
UID
Reset reason
Metadata

Memory map:

actual B300 target layout.

Protection check:

actual state.

Metadata:

actual metadata model.

Primary:

[KIỂM TRA TARGET]

Secondary:

[Target Doctor]
[Đọc metadata]
[Xuất evidence]

Không có flash/application write action tại DEVICE.

DEVICE phải read-only.


==================================================
SETTINGS PAGE
==================================================

SETTINGS là configuration/control center.

Sections:

1. Shared Resources

[Quản lý kết nối]
[Quản lý dự án]

Có thể show compact preview list.

2. Runtime / Toolchain

OpenOCD
ARM GDB
VS Code
Cortex-Debug

Chỉ show tool thật B300 cần.

Không thêm Python/CMake/Git/STM32CubeTools vào UI chỉ vì ảnh mockup có,
nếu runtime product không yêu cầu người dùng quản lý chúng.

Audit core trước.

3. Gateway Host Setup

This PC as Gateway

OpenSSH Server
OpenOCD runtime
ST-Link access
firewall/readiness

[Prepare]
[Diagnose]

Không trộn với Client Gateway Manager.

4. Machine Setup

Windows driver
Linux udev
OpenSSH Client
managed GDB/OpenOCD

5. Update

Current version
Latest version
Check update
Release notes

6. Appearance

Theme
Density

Language chỉ nếu app đã thực sự support i18n.
Không tạo fake language feature.

7. Support

Support bundle
Open logs
About
Documentation


==================================================
RIGHT SIDEBAR
==================================================

Reference hiện có right information sidebar khá đẹp.

Có thể giữ.

Nhưng tránh duplicate toàn bộ thông tin với main panel.

Sidebar chỉ nên là:

quick summary
session status
last operation

Ví dụ PROGRAM:

Probe
Target
Protection
Firmware
Last flash

MONITOR:

Probe
Target
Connection
Project
Monitor status

DEBUG VS CODE:

Connection
OpenOCD
GDB
Workspace
Last launch

DEVICE:

Probe
Target
Voltage
Protection
Last inspect

SETTINGS:

App version
Update
Support


==================================================
LOG UX
==================================================

Không để log chiếm quá nhiều diện tích mặc định.

Cho:

collapsed / expandable

hoặc panel thấp 120–180 px.

Buttons:

Clear
Save
Level

Logs dành cho engineer,
nhưng primary workflow không được phụ thuộc việc đọc log.


==================================================
WINDOW RESPONSIVENESS
==================================================

Phải test ít nhất:

1366x768
1600x900
1920x1080
2560x1440

Không hardcode UI chỉ đẹp ở screenshot 1672x941.

Không để:

- text clip
- button overlap
- panel overflow
- horizontal scroll toàn app

Table/chart có thể stretch.

Right sidebar có min/max width.

Sidebar trái fixed/compact.

Central workspace stretch.


==================================================
SAFETY — KHÔNG ĐƯỢC ĐỤNG
==================================================

Frontend refactor không được làm thay đổi B300 flash safety.

F407 canonical safety:

Sector 0–2:
Bootloader
→ normal Application flow không erase/program.

Sector 3:
OTA metadata
→ canonical metadata transaction only.

Sector 4–7:
Application permitted.

Không được:

mass erase
chip erase
bypass WRP
bypass RDP validation
raw flash
blind retry
change Option Bytes
weaken target identity validation
bypass HardwareSession

PROGRAM preflight v0.19.1 phải còn nguyên semantics.

HW-P1-001 vẫn:

OPEN / DEFERRED.

Không đổi thành PASS.


==================================================
BACKEND RULE
==================================================

Không rewrite backend chỉ để phù hợp UI.

Ưu tiên:

View
Controller
shared state adapter
reusable components

Core service giữ nguyên nếu không có bug thật.

Nếu thấy UI hiện tại thiếu API dùng chung:

hãy tạo abstraction nhỏ,
không duplicate state.


==================================================
CODE QUALITY
==================================================

Không tạo một file MainWindow khổng lồ.

Nên phân tách reusable components:

SharedContextBar
StatusCard
SectionCard
ToolStatusCard
DeviceSummarySidebar
ActivityLogPanel
ConnectionManagerDialog
ProjectManagerDialog

Pages:

ProgramView
MonitorView
VsCodeDebugView
DeviceView
SettingsView

Central shared model:

AppContext / AppState

Không copy-paste cùng một widget implementation 5 lần.


==================================================
MIGRATION
==================================================

Existing saved Gateway profiles phải migrate được.

Existing Project profile phải migrate được.

Existing settings không được mất.

Nếu schema thay đổi:

implement migration.

Không xóa config của người dùng.


==================================================
TEST YÊU CẦU
==================================================

Phải bổ sung/update tests cho:

NAVIGATION
- exactly 5 main pages
- PROGRAM
- MONITOR
- DEBUG VS CODE
- DEVICE
- SETTINGS

SHARED CONTEXT
- Project change propagate tất cả page
- Connection change propagate tất cả page
- Probe change invalidate target info nếu cần
- target info shared correctly

CONNECTION
- no Local/Gateway/Client selectors exposed
- Gateway session reused MONITOR ↔ DEBUG
- password not persisted
- app close clears session

PROJECT
- PROGRAM resolves HEX
- MONITOR resolves ELF/AXF
- DEBUG VS CODE resolves Workspace/ELF

PROGRAM
- v0.19.1 auto-preflight preserved
- WRP fail blocks write
- RDP fail blocks write
- target mismatch blocks write
- no Device navigation required

MONITOR
- zero-halt behavior unchanged
- start/stop
- refresh intervals
- variable list
- no duplicate ELF chooser

DEBUG VS CODE
- connection/profile used automatically
- VS Code bridge launch
- no redundant Host/User/Port fields
- no Local/Gateway/Client selector

DEVICE
- read-only
- target inspect state shared

SETTINGS
- Connection Manager
- Project Manager

LAYOUT
- smoke tests on supported screen sizes where feasible

Run:

python -m compileall ...
git diff --check

Then canonical isolated unittest runner.

Full regression must pass.


==================================================
KHÔNG LÀM
==================================================

Không:

- phát hành ngay
- bump version ngay
- redesign backend
- add fake functionality from image
- add fake hardware metrics
- duplicate selectors
- recreate IDE
- replace VS Code debugger
- save passwords
- weaken safety


==================================================
WORKFLOW THỰC HIỆN
==================================================

PHASE 1
Audit current GUI and report:

- duplicated controls
- current ownership
- reusable components
- obsolete UI
- migration impact

PHASE 2
Write concise implementation plan.

PHASE 3
Implement shared AppContext + SharedContextBar first.

PHASE 4
Refactor navigation:

PROGRAM
MONITOR
DEBUG VS CODE
DEVICE
SETTINGS

PHASE 5
Refactor each page against reference design.

PHASE 6
Remove obsolete/hidden duplicate UI only after consumers migrate.

PHASE 7
Tests.

PHASE 8
Full regression.

PHASE 9
Run application and capture actual screenshots of all 5 pages:

PROGRAM
MONITOR
DEBUG VS CODE
DEVICE
SETTINGS

Compare actual screenshots against reference visually.

PHASE 10
Report back.

Do NOT release until I review screenshots.


==================================================
DEFINITION OF DONE
==================================================

Tôi phải có:

1. frontend nhìn đồng nhất với reference
2. MONITOR rộng, dễ đọc
3. DEBUG VS CODE riêng
4. Project chọn một lần
5. Connection chọn một lần
6. không còn Local/Gateway/Client selector lặp
7. không còn Host/User/Port nằm rải rác
8. không còn Workspace/ELF chooser lặp
9. Gateway login/session dùng chung
10. DEVICE read-only
11. PROGRAM auto-preflight
12. safety core không đổi
13. responsive 1366 → 2560
14. full regression PASS
15. screenshots thực tế để tôi nghiệm thu

Khi hoàn thành:
KHÔNG hỏi tôi có muốn release ngay không.

Hãy dừng ở trạng thái:

IMPLEMENTATION READY FOR UI REVIEW

và cung cấp:

- branch
- commit
- files changed
- architecture before/after
- test results
- 5 screenshots thực tế
- known differences so với reference
- remaining issues