# Project Rules — AI_factory

## 1. Cấm Hardcode (No Hardcoding)
- **Cấu hình & Hạ tầng:** Không hardcode URL, host, port, đường dẫn tuyệt đối trên máy local, mật khẩu, API key, token, secret, hay tên bucket/database trong source code hoặc Dockerfile. Mọi giá trị cấu hình phải đọc từ `.env`, biến môi trường, hoặc `platform_core.settings` / relative gateway path (ví dụ `/api/core`, `/ba/api`, `/designer/api/v1`).
- **Dữ liệu nghiệp vụ & Prompt Agent:** Không hardcode tên dự án, tên feature, tên màn hình, actor, business rule, hoặc kịch bản cụ thể của một dự án vào code logic hay system prompt dùng chung của BA/Designer agent. Logic và prompt phải tổng quát (domain-agnostic) và đọc từ dữ liệu động của project/session/Project Wiki.
- **UI & Hằng số dùng lại:** Không hardcode các chuỗi lặp lại, magic numbers (timeout, kích thước, ngưỡng kiểm tra), hoặc danh sách cấu hình/trạng thái rải rác nhiều nơi; phải gom về hằng số dùng chung, config, hoặc lấy từ API backend.
- **Tài khoản & Danh tính:** Không hardcode `user_id` hoặc `actor` giả ở frontend/backend khi đã có phiên đăng nhập; luôn lấy danh tính từ user đã xác thực.
