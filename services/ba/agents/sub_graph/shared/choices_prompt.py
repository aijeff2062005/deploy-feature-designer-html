"""Shared prompt instruction for emitting interactive user choice cards."""

USER_CHOICES_INSTRUCTION = (
    "KHUNG LỰA CHỌN TƯƠNG TÁC (CHỈ dùng cho quyết định nghiệp vụ / Open Questions cần người dùng chốt):\n"
    "- CHỈ gắn `<!-- AGENT_CHOICES_JSON:... -->` khi cần người dùng chọn giữa 2+ **phương án nghiệp vụ cụ thể** "
    "(ví dụ: quy tắc xử lý lỗi, timeout, điều kiện rẽ nhánh, phạm vi, hoặc chốt câu hỏi tồn đọng OQ) "
    "HOẶC xác nhận một cập nhật artifact cụ thể.\n"
    "- **CẤM TUYỆT ĐỐI** tạo `AGENT_CHOICES_JSON` cho các thao tác hệ thống/giao diện như: "
    "'Chạy Validate?', 'Xem Preview BPMN?', 'Chuyển sang section tiếp theo?', 'Export tài liệu?', 'Bạn có muốn tiếp tục không?' "
    "(giao diện đã có thanh nút bấm nhanh riêng cho các thao tác này).\n"
    "- Định dạng ẩn ở cuối cùng của câu trả lời (KHÔNG bọc trong code block ```):\n"
    "  * 1 câu hỏi:\n"
    '    <!-- AGENT_CHOICES_JSON:{"question":"Câu hỏi ngắn gọn","multi":false,"options":[{"label":"Nhãn ngắn","value":"Câu chỉ dẫn đầy đủ bằng tiếng Việt để hệ thống thực hiện","recommended":true},{"label":"Phương án khác","value":"Câu chỉ dẫn đầy đủ bằng tiếng Việt"}]} -->\n'
    "  * Nhiều câu hỏi nghiệp vụ cần chốt cùng lúc (tối đa 4 câu trong 1 lượt để giảm số vòng chat):\n"
    '    <!-- AGENT_CHOICES_JSON:{"questions":[{"question":"Câu 1?","multi":false,"options":[{"label":"...","value":"...","recommended":true},{"label":"...","value":"..."}]},{"question":"Câu 2?","multi":false,"options":[{"label":"...","value":"..."},{"label":"..."}]}]} -->\n'
    "- TUYỆT ĐỐI KHÔNG dùng mã slug/từ khóa kỹ thuật (như `update_and_review`, `preview_bpmn`, `skip`, `snake_case`) trong trường `value`. "
    "`value` PHẢI là câu tiếng Việt đầy đủ, rõ nghĩa và nêu rõ hành động/đối tượng để bước trích xuất có thể thực thi ngay.\n"
    "- KHÔNG lặp lại câu hỏi: trước khi hỏi, kiểm tra lịch sử hội thoại gần nhất và artifact hiện tại — tuyệt đối KHÔNG hỏi lại câu hỏi hoặc lựa chọn mà người dùng đã trả lời/chọn ở lượt trước.\n"
    "- TUYỆT ĐỐI KHÔNG gắn block này khi đang chào hỏi, giải thích thông tin chung, hoặc đặt câu hỏi mở để thu thập dữ liệu (ví dụ hỏi tên tác nhân, mô tả màn hình, quy tắc nghiệp vụ...).\n"
    "- Nếu bạn liệt kê các mục gợi ý còn thiếu hoặc các lỗi cần sửa (ví dụ 2+ mục đánh số 1, 2, 3...), hãy gắn `AGENT_CHOICES_JSON` với `\"multi\": true` liệt kê từng mục đó để người dùng chọn các mục muốn sửa cùng lúc.\n"
    "- Đặt `multi: true` chỉ khi người dùng có thể chọn nhiều phương án cùng lúc; tối đa 5 options mỗi câu; KHÔNG thêm mục 'Khác' vào JSON vì giao diện đã tự có sẵn ô nhập Khác.\n"
    "- Không nhắc tới hay giải thích thẻ `AGENT_CHOICES_JSON` trong nội dung trả lời cho người dùng."
)
