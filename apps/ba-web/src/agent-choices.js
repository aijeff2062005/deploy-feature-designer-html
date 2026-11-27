/**
 * Pure Agent-driven choice-card parser (Antigravity-style).
 * Only renders a choice card when the agent explicitly emits <!-- AGENT_CHOICES_JSON:... -->.
 */

const CHOICE_TAG_RE = /<!--\s*AGENT[\s_-]+CHOICES[\s_-]+JSON\s*:([\s\S]*?)-->/gi;
const FENCED_CHOICE_TAG_RE = /```(?:html|json)?\s*<!--\s*AGENT[\s_-]+CHOICES[\s_-]+JSON\s*:[\s\S]*?-->\s*```/gi;
const PARTIAL_CHOICE_TAG_RE = /(?:```(?:html|json)?\s*)?<!--\s*AGENT[\s_-]+CHOICES[\s_-]+JSON\s*:[\s\S]*$/i;

export function stripAgentChoicesTag(text) {
  return String(text || '')
    .replace(FENCED_CHOICE_TAG_RE, '')
    .replace(CHOICE_TAG_RE, '')
    .replace(PARTIAL_CHOICE_TAG_RE, '')
    .trim();
}

function cleanText(raw) {
  return String(raw || '').replace(/\*\*/g, '').replace(/\s+/g, ' ').trim();
}

function isSlug(raw) {
  const s = String(raw || '').trim();
  return Boolean(s) && !/\s/.test(s) && /^[a-z0-9_:-]+$/i.test(s);
}

function normalizeQuestionItem(item) {
  if (!item || !Array.isArray(item.options) || item.options.length < 2) return null;
  const options = item.options
    .map((opt) => {
      if (typeof opt === 'string') {
        const label = cleanText(opt);
        return { label, value: label, recommended: false };
      }
      const label = cleanText(opt.label || opt.value || '');
      const rawValue = String(opt.value || '').trim();
      const value =
        !rawValue || isSlug(rawValue) || (label.length > rawValue.length && rawValue.split(/\s+/).length < 2)
          ? label
          : rawValue;
      return { label, value, recommended: Boolean(opt.recommended) };
    })
    .filter((opt) => opt.label && opt.value)
    .slice(0, 6);

  if (options.length < 2) return null;
  return {
    question: cleanText(item.question || 'Chọn phương án bạn muốn thực hiện'),
    multi: Boolean(item.multi ?? item.is_multi_select),
    options,
  };
}

export function extractAgentChoices(text) {
  const raw = String(text || '');
  if (!raw.trim()) return null;

  for (const match of raw.matchAll(CHOICE_TAG_RE)) {
    try {
      const parsed = JSON.parse(match[1]);
      const rawItems = Array.isArray(parsed?.questions) ? parsed.questions : [parsed];
      const questions = rawItems.map(normalizeQuestionItem).filter(Boolean).slice(0, 4);
      if (questions.length > 0) {
        return {
          questions,
          signature: questions.map((q) => q.question).join(' | '),
          question: questions[0].question,
          multi: questions[0].multi,
          options: questions[0].options,
        };
      }
    } catch {
      /* ignore malformed JSON tag */
    }
  }

  return null;
}

const PROCEDURAL_QUESTION_RE = /(chạy\s+validate|xác nhận\s+.*validate|preview\s*bpmn|xem\s+.*bpmn|chuyển\s+sang\s+sec|xuất\s+tài\s+liệu|bàn\s+giao\s+designer)/i;
const BUSINESS_SIGNAL_RE = /(pnr|pin|voucher|giá|phí|hết hạn|khóa|lỗi|thanh toán|chiết khấu|quy tắc|actor|luồng|điều kiện|xử lý|trạng thái)/i;

export function isProceduralChoicePrompt(batch) {
  const questions = Array.isArray(batch?.questions) ? batch.questions : [];
  if (!questions.length) return false;
  return questions.every((q) => {
    const text = String(q?.question || '');
    const optText = (q?.options || []).map((o) => `${o.label} ${o.value}`).join(' ');
    const looksProcedural = PROCEDURAL_QUESTION_RE.test(text) || PROCEDURAL_QUESTION_RE.test(optText);
    const hasBusinessDecision = BUSINESS_SIGNAL_RE.test(text);
    return looksProcedural && !hasBusinessDecision;
  });
}

export function formatChoiceSubmission(selectedValues, customText) {
  const parts = selectedValues.map((v) => String(v || '').trim()).filter(Boolean);
  const custom = String(customText || '').trim();
  if (custom) parts.push(custom);
  if (parts.length <= 1) return parts[0] || '';
  return `Tôi chọn các phương án sau:\n${parts.map((p) => `- ${p}`).join('\n')}`;
}

export function formatBatchSubmission(questions, answersByIdx) {
  if (!Array.isArray(questions) || questions.length <= 1) {
    const ans = answersByIdx?.[0] || {};
    return formatChoiceSubmission(
      Array.from(ans.selected || []),
      ans.useCustom ? ans.customText : '',
    );
  }
  const lines = [];
  questions.forEach((q, idx) => {
    const ans = answersByIdx?.[idx];
    if (!ans) return;
    const parts = Array.from(ans.selected || []).map((v) => String(v || '').trim()).filter(Boolean);
    const custom = ans.useCustom ? String(ans.customText || '').trim() : '';
    if (custom) parts.push(custom);
    if (parts.length > 0) {
      lines.push(`${idx + 1}. ${q.question}: ${parts.join('; ')}`);
    }
  });
  if (!lines.length) return '';
  if (lines.length === 1) return lines[0].replace(/^\d+\.\s*/, '');
  return `Tôi chốt các phương án sau:\n${lines.join('\n')}`;
}

const SKIP_ACTOR_RE = /(bỏ qua|giữ nguyên|tự làm|tự chỉnh sửa|để agent)/i;
const UPDATE_ACTION_RE = /(cập nhật|bổ sung|thêm\b|chỉnh sửa|sửa\b|thay đổi|tạo mới|viết lại|điều chỉnh|chuẩn hóa|chuyển đổi|ánh xạ|khắc phục|update|edit|add\b)/i;

export function isUpdateChoice(text) {
  const value = String(text || '').trim();
  if (!value || SKIP_ACTOR_RE.test(value)) return false;
  return UPDATE_ACTION_RE.test(value);
}

const FIX_LIST_CUE_RE = /(cần sửa|cần khắc phục|vấn đề sau|cần xử lý|cần cập nhật|cần điều chỉnh|lỗi hiện tại|cần hoàn thiện)/i;
const SUGGESTION_CUE_RE = /(gợi ý|còn thiếu|cần xem xét|cần bổ sung|nên bổ sung|đề xuất|khuyến nghị|thiếu sót)/i;

/**
 * Fallback: when the agent lists fix/suggestion items or the section still has failed flags
 * without emitting AGENT_CHOICES_JSON, build a multi-select Choice Card so the user can
 * pick items and choose "Để Agent tự làm" vs "Để tôi tự làm".
 */
export function extractFeedbackFixChoices(lastMsgText, secFlags = [], rawFlagValues = {}) {
  const text = stripAgentChoicesTag(lastMsgText);
  const options = [];
  const seenLabels = new Set();
  let usedFix = false;
  let usedSuggest = false;

  const pushOption = (rawTitle, rawDetail, mode) => {
    const title = cleanText(rawTitle).replace(/`+/g, '').replace(/[:.—–-]+$/, '').slice(0, 72);
    const detail = cleanText(rawDetail).replace(/`+/g, '').slice(0, 220);
    if (!title) return;
    const key = title.toLowerCase();
    if (seenLabels.has(key)) return;
    seenLabels.add(key);
    const prefix = mode === 'suggest' ? 'Bổ sung' : 'Sửa';
    if (mode === 'suggest') usedSuggest = true;
    else usedFix = true;
    options.push({
      label: title,
      value: detail && detail !== title ? `${prefix} ${title}: ${detail}` : `${prefix} ${title}`,
      recommended: true,
    });
  };

  if (text) {
    const lines = text.split('\n');
    let captureMode = null;
    for (const line of lines) {
      const trimmed = line.trim();
      const isHeading = /^#{1,4}\s+/.test(trimmed) || /^\*\*\d+[.)]\s+/.test(trimmed);
      if (FIX_LIST_CUE_RE.test(trimmed)) {
        captureMode = 'fix';
        continue;
      }
      if (SUGGESTION_CUE_RE.test(trimmed)) {
        captureMode = 'suggest';
        continue;
      }
      if (isHeading) {
        captureMode = null;
        continue;
      }
      if (!captureMode) continue;

      const bulletMatch = line.match(/^\s*[-*•]\s+\*\*([^*]+)\*\*\s*:?\s*(.*)$/);
      if (bulletMatch) {
        pushOption(bulletMatch[1], bulletMatch[2], captureMode);
        if (options.length >= 6) break;
        continue;
      }

      const numMatch = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (numMatch) {
        const body = numMatch[1].trim();
        const boldMatch = body.match(/^\*\*([^*]+)\*\*\s*:?\s*(.*)$/);
        if (boldMatch || captureMode === 'fix') {
          const rawTitle = boldMatch ? boldMatch[1] : body.split(/[:.—–-]/)[0];
          const rawDetail = boldMatch ? boldMatch[2] : body;
          pushOption(rawTitle, rawDetail, captureMode);
          if (options.length >= 6) break;
        }
      }
    }
  }

  if (options.length === 0 && text && FIX_LIST_CUE_RE.test(text)) {
    const lines = text.split('\n');
    for (const line of lines) {
      const m = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (!m) continue;
      const body = m[1].trim();
      const boldMatch = body.match(/^\*\*([^*]+)\*\*\s*:?\s*(.*)$/);
      const rawTitle = boldMatch ? boldMatch[1] : body.split(/[:.—–-]/)[0];
      const rawDetail = boldMatch ? boldMatch[2] : body;
      pushOption(rawTitle, rawDetail, 'fix');
      if (options.length >= 6) break;
    }
  }

  if (options.length === 0 && Array.isArray(secFlags) && rawFlagValues && typeof rawFlagValues === 'object') {
    for (const flag of secFlags) {
      const raw = rawFlagValues[flag.id];
      const status = String(typeof raw === 'object' && raw ? raw.status : raw || '').toUpperCase();
      if (status !== 'FAIL' && status !== 'NOT_YET_REASONABLE') continue;
      const detail = cleanText(typeof raw === 'object' && raw ? raw.detail : '').replace(/`+/g, '').slice(0, 220);
      const label = cleanText(flag.label || flag.id);
      const key = label.toLowerCase();
      if (!label || seenLabels.has(key)) continue;
      seenLabels.add(key);
      usedFix = true;
      options.push({
        label,
        value: detail ? `Sửa lỗi ${label}: ${detail}` : `Sửa lỗi ${label}`,
        recommended: true,
      });
      if (options.length >= 6) break;
    }
  }

  if (options.length === 0) return null;
  const isSuggestOnly = usedSuggest && !usedFix;
  const q = {
    question: isSuggestOnly ? 'Chọn các mục bạn muốn bổ sung / cập nhật' : 'Chọn các mục bạn muốn sửa',
    multi: true,
    options,
  };
  return {
    questions: [q],
    signature: `${isSuggestOnly ? 'suggest' : 'fix'}:${options.map((o) => o.label).join('|')}`,
    question: q.question,
    multi: true,
    options: q.options,
  };
}

const LOCAL_DISMISS_RE = /(?:để tôi tự|tôi sẽ tự|tôi tự\s+(?:làm|sửa|chỉnh|kiểm tra|xem|cập nhật|bổ sung)|không,\s*tôi\s+(?:sẽ\s+)?tự|không cần\b)/i;

export function isLocalDismissChoice(text) {
  return LOCAL_DISMISS_RE.test(String(text || '').trim());
}
