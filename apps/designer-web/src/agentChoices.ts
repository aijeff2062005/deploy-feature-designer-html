export interface AgentChoiceOption {
    label: string;
    value: string;
    recommended?: boolean;
}

export interface AgentChoicePrompt {
    question: string;
    multi: boolean;
    options: AgentChoiceOption[];
}

const CHOICE_TAG_RE = /<!--\s*AGENT[\s_-]+CHOICES[\s_-]+JSON\s*:([\s\S]*?)-->/gi;
const FENCED_CHOICE_TAG_RE = /```(?:html|json)?\s*<!--\s*AGENT[\s_-]+CHOICES[\s_-]+JSON\s*:[\s\S]*?-->\s*```/gi;
const PARTIAL_CHOICE_TAG_RE = /(?:```(?:html|json)?\s*)?<!--\s*AGENT[\s_-]+CHOICES[\s_-]+JSON\s*:[\s\S]*$/i;

export function stripAgentChoicesTag(text: string): string {
    return String(text || "")
        .replace(FENCED_CHOICE_TAG_RE, "")
        .replace(CHOICE_TAG_RE, "")
        .replace(PARTIAL_CHOICE_TAG_RE, "")
        .trim();
}

function cleanText(raw: string): string {
    return String(raw || "").replace(/\*\*/g, "").replace(/\s+/g, " ").trim();
}

function isSlug(raw: string): boolean {
    const s = String(raw || "").trim();
    return Boolean(s) && !/\s/.test(s) && /^[a-z0-9_:-]+$/i.test(s);
}

type RawOption = string | { label?: string; value?: string; recommended?: boolean };
type RawPrompt = {
    question?: string;
    multi?: boolean;
    is_multi_select?: boolean;
    options?: RawOption[];
    questions?: RawPrompt[];
};

export function extractAgentChoices(text: string): AgentChoicePrompt | null {
    const raw = String(text || "");
    if (!raw.trim()) return null;

    for (const match of raw.matchAll(CHOICE_TAG_RE)) {
        try {
            const parsed = JSON.parse(match[1]) as RawPrompt;
            const item = Array.isArray(parsed?.questions) ? parsed.questions[0] : parsed;
            if (!item || !Array.isArray(item.options) || item.options.length < 2) continue;

            const options: AgentChoiceOption[] = item.options
                .map((opt) => {
                    if (typeof opt === "string") {
                        const label = cleanText(opt);
                        return { label, value: label, recommended: false };
                    }
                    const label = cleanText(opt.label || opt.value || "");
                    const rawValue = String(opt.value || "").trim();
                    const value =
                        !rawValue || isSlug(rawValue) || (label.length > rawValue.length && rawValue.split(/\s+/).length < 2)
                            ? label
                            : rawValue;
                    return { label, value, recommended: Boolean(opt.recommended) };
                })
                .filter((opt) => opt.label && opt.value)
                .slice(0, 6);

            if (options.length >= 2) {
                return {
                    question: cleanText(item.question || "Chọn phương án bạn muốn thực hiện"),
                    multi: Boolean(item.multi ?? item.is_multi_select),
                    options,
                };
            }
        } catch {
            /* ignore malformed JSON tag */
        }
    }

    return null;
}

export function formatChoiceSubmission(selectedValues: string[], customText: string): string {
    const parts = selectedValues.map((v) => String(v || "").trim()).filter(Boolean);
    const custom = String(customText || "").trim();
    if (custom) parts.push(custom);
    if (parts.length <= 1) return parts[0] || "";
    return `Tôi chọn các phương án sau:\n${parts.map((p) => `- ${p}`).join("\n")}`;
}

const SKIP_ACTOR_RE = /(bỏ qua|giữ nguyên|tự làm|tự chỉnh sửa|để agent)/i;
const UPDATE_ACTION_RE = /(cập nhật|bổ sung|thêm\b|chỉnh sửa|sửa\b|thay đổi|tạo mới|viết lại|điều chỉnh|update|edit|add\b)/i;

export function isUpdateChoice(text: string): boolean {
    const value = String(text || "").trim();
    if (!value || SKIP_ACTOR_RE.test(value)) return false;
    return UPDATE_ACTION_RE.test(value);
}

const LOCAL_DISMISS_RE = /(?:để tôi tự|tôi sẽ tự|tôi tự\s+(?:làm|sửa|chỉnh|kiểm tra|xem|cập nhật|bổ sung)|không,\s*tôi\s+(?:sẽ\s+)?tự|không cần\b)/i;

export function isLocalDismissChoice(text: string): boolean {
    return LOCAL_DISMISS_RE.test(String(text || "").trim());
}
