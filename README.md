# Unified Work AI Platform

Repository chạy một nền tảng gồm Portal, BA agent và Designer agent. Hướng dẫn này tập trung vào cách dựng môi trường local để phát triển và kiểm tra ứng dụng.

## Yêu cầu

- Git
- Docker Desktop (Windows/macOS) hoặc Docker Engine và Docker Compose v2 (Linux)
- Kết nối Internet để tải image và build lần đầu
- API key cho LLM để sử dụng các chức năng AI. Có thể khởi động nền tảng trước khi cấu hình key, nhưng chat/thiết kế AI sẽ không hoạt động đầy đủ.

> Trên Windows, mở PowerShell tại thư mục repository. Các lệnh bên dưới dùng cú pháp PowerShell.

## Khởi động toàn bộ nền tảng

### 1. Tạo file cấu hình

```powershell
Copy-Item .env.example .env
```

Mở `.env` và cấu hình ít nhất các giá trị sau cho môi trường local:

```dotenv
LLM_API_KEY=<API key của LLM gateway>
DESIGNER_LLM_API_KEY=<API key nếu Designer dùng gateway riêng; để trống để dùng LLM_API_KEY>
```

Các thiết lập mặc định đã dùng PostgreSQL trong Docker, local storage và mock authentication. Không cần cài PostgreSQL riêng. `.env` đã được gitignore; không commit file này hoặc API key.

### 2. Build và chạy

```powershell
docker compose up -d --build
```

Lần đầu sẽ mất thêm thời gian để tải image và cài dependencies. Kiểm tra các container:

```powershell
docker compose ps
```

Mở Portal tại <http://localhost:8080/>.

### 3. Đăng nhập

Mặc định `AUTH_MODE=mock`, mật khẩu demo là `demo123`:

| Tài khoản | Vai trò |
|---|---|
| `pm.hoa` | PM |
| `lead.dung` | PM |

Chế độ `MOCK_SEED=minimal` không tạo project mẫu. Đăng nhập rồi tạo project trong Portal. Để nạp thêm tài khoản theo role và project mẫu, đặt `MOCK_SEED=full` trong `.env`, sau đó chạy lại:

```powershell
docker compose up -d
```

## Địa chỉ ứng dụng

| Đường dẫn | Nội dung |
|---|---|
| <http://localhost:8080/> | Portal, đăng nhập và danh sách project |
| <http://localhost:8080/ba/> | BA web |
| <http://localhost:8080/designer/> | Designer studio |
| <http://localhost:8080/designer/canvas.html> | Designer canvas |

Các API được gateway chuyển tiếp theo đường dẫn `/api/core`, `/ba/api` và `/designer/api/v1`. Khi chạy Compose, chỉ cần truy cập ứng dụng qua gateway tại cổng `8080` (hoặc cổng đặt trong `PLATFORM_GATEWAY_PORT`).

## Chạy riêng một module

Các file compose module vẫn khởi động hạ tầng dùng chung, gateway, Core API và Portal. Chạy một trong các lệnh sau từ thư mục gốc:

```powershell
# BA cùng Portal và hạ tầng
docker compose -f compose.ba.yml up -d --build

# Designer cùng Portal và hạ tầng
docker compose -f compose.designer.yml up -d --build
```

Muốn chuyển lại chạy toàn bộ nền tảng, dùng `docker compose up -d --build`.

## Theo dõi và quản lý

```powershell
# Trạng thái container
docker compose ps

# Theo dõi log của mọi service
docker compose logs -f

# Theo dõi log của một service
docker compose logs -f core-api
docker compose logs -f ba-api
docker compose logs -f designer-api

# Build và khởi động lại sau khi đổi Dockerfile hoặc dependencies
docker compose up -d --build

# Dừng container, giữ lại database và storage volumes
docker compose down
```

Không thêm `-v` vào lệnh `down` nếu muốn giữ database và dữ liệu local.

## Phát triển frontend

Ba frontend nằm trong `apps/portal`, `apps/ba-web` và `apps/designer-web`. Image của từng frontend build bằng Dockerfile trong thư mục tương ứng; gateway hiện phục vụ các bản build đó. Sau khi sửa frontend, cập nhật bản đang chạy bằng:

```powershell
docker compose up -d --build portal-web ba-web designer-web
```

Các app dùng npm và có script `npm run dev` để chạy Vite riêng. Với chế độ này, cần bảo đảm backend mà Vite proxy gọi tới đang chạy và các biến môi trường base/API của từng app trỏ đúng gateway hoặc backend local.

## Cấu trúc repository

```text
apps/                    Portal, BA web, Designer web
services/core/           API tài khoản, project, thành viên và artifacts
services/ba/             BA agent và API
services/designer/       Designer agent, API và Huly sidecars
packages/platform_core/  Cấu hình dùng chung, database, auth và storage
packages/wiki/            Project/company wiki
infra/                    PostgreSQL, gateway và cấu hình nginx
scripts/                  Công cụ e2e, demo và quản trị dữ liệu
```

## Test

Các test chạy trong Docker Compose profile `test`. Ví dụ:

```powershell
docker compose --profile test run --rm core-test
docker compose --profile test run --rm ba-test
docker compose --profile test run --rm designer-test
```

## Cấu hình chính

`.env.example` là danh sách cấu hình tham khảo. Các nhóm thường cần biết:

- `PLATFORM_GATEWAY_PORT`: cổng truy cập web local, mặc định `8080`.
- `POSTGRES_*`: thông tin PostgreSQL. Compose tự đặt hostname nội bộ cho các service.
- `AUTH_MODE`, `MOCK_SEED`: chế độ đăng nhập và dữ liệu demo.
- `LLM_*`, `BA_LLM_MODEL`, `DESIGNER_LLM_*`: gateway và model AI.
- `STORAGE_BACKEND`: `local` cho môi trường dev; `s3` khi dùng object storage.
- `HULY_TOKEN`, `MCP_AUTH_TOKEN`: chỉ cấu hình nếu cần tích hợp Huly; giữ bí mật các giá trị này.
