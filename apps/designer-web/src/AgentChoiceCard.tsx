import { ArrowLeft, ArrowRight, Check, X } from "lucide-react";
import { useEffect, useState } from "react";
import { formatChoiceSubmission, isLocalDismissChoice, isUpdateChoice, type AgentChoicePrompt } from "./agentChoices";

export function AgentChoiceCard({
    prompt,
    disabled = false,
    onSubmit,
    onDismiss,
}: {
    prompt: AgentChoicePrompt;
    disabled?: boolean;
    onSubmit: (answer: string) => void;
    onDismiss: () => void;
}) {
    const [selected, setSelected] = useState<string[]>([]);
    const [useCustom, setUseCustom] = useState(false);
    const [customText, setCustomText] = useState("");
    const [pendingConfirmText, setPendingConfirmText] = useState("");

    useEffect(() => {
        setSelected([]);
        setUseCustom(false);
        setCustomText("");
        setPendingConfirmText("");
    }, [prompt]);

    const toggleOption = (value: string) => {
        if (prompt.multi) {
            setSelected((prev) => (prev.includes(value) ? prev.filter((item) => item !== value) : [...prev, value]));
        } else {
            setSelected([value]);
            setUseCustom(false);
        }
    };

    const canSubmit = !disabled && (selected.length > 0 || (useCustom && customText.trim().length > 0));
    const allSelected = prompt.multi && prompt.options.every((opt) => selected.includes(opt.value));

    const handleSubmit = () => {
        if (!canSubmit) return;
        const message = formatChoiceSubmission(selected, useCustom ? customText : "");
        if (!message) return;
        if (isLocalDismissChoice(message)) {
            onDismiss();
            return;
        }
        if (isUpdateChoice(message)) {
            setPendingConfirmText(message);
            return;
        }
        onSubmit(message);
    };

    if (pendingConfirmText) {
        return (
            <section className="agent-choice-card is-single" aria-label="Xác nhận người thực hiện cập nhật">
                <header className="agent-choice-head">
                    <div className="agent-choice-title-wrap">
                        <strong>Bạn muốn ai thực hiện cập nhật này?</strong>
                        <small className="agent-choice-confirm-summary">{pendingConfirmText}</small>
                    </div>
                    <button
                        type="button"
                        className="agent-choice-close"
                        onClick={onDismiss}
                        aria-label="Ẩn khung lựa chọn"
                        title="Ẩn khung lựa chọn"
                    >
                        <X size={14} />
                    </button>
                </header>
                <div className="agent-choice-list" role="radiogroup">
                    <button
                        type="button"
                        className="agent-choice-opt"
                        disabled={disabled}
                        onClick={() => onSubmit(pendingConfirmText)}
                    >
                        <span className="agent-choice-num">1</span>
                        <span className="agent-choice-label">Để Agent tự làm</span>
                        <span className="agent-choice-badge">Đề xuất</span>
                    </button>
                    <button
                        type="button"
                        className="agent-choice-opt"
                        disabled={disabled}
                        onClick={onDismiss}
                    >
                        <span className="agent-choice-num">2</span>
                        <span className="agent-choice-label">Để tôi tự làm</span>
                    </button>
                </div>
                <footer className="agent-choice-foot">
                    <button
                        type="button"
                        className="agent-choice-back"
                        onClick={() => setPendingConfirmText("")}
                        disabled={disabled}
                    >
                        <ArrowLeft size={13} /> Quay lại
                    </button>
                </footer>
            </section>
        );
    }

    return (
        <section
            className={`agent-choice-card ${prompt.multi ? "is-multi" : "is-single"}`}
            aria-label="Lựa chọn nhanh cho agent"
        >
            <header className="agent-choice-head">
                <div className="agent-choice-title-wrap">
                    <strong>{prompt.question}</strong>
                    <small>{prompt.multi ? "Có thể chọn nhiều phương án" : "Chọn 1 phương án"}</small>
                </div>
                <button
                    type="button"
                    className="agent-choice-close"
                    onClick={onDismiss}
                    aria-label="Ẩn khung lựa chọn"
                    title="Ẩn khung lựa chọn"
                >
                    <X size={14} />
                </button>
            </header>

            <div className="agent-choice-list" role={prompt.multi ? "group" : "radiogroup"}>
                {prompt.options.map((opt, index) => {
                    const isSelected = selected.includes(opt.value);
                    return (
                        <button
                            key={`${opt.value}-${index}`}
                            type="button"
                            className={`agent-choice-opt${isSelected ? " is-selected" : ""}`}
                            onClick={() => toggleOption(opt.value)}
                            disabled={disabled}
                            aria-pressed={isSelected}
                        >
                            <span className="agent-choice-num">{index + 1}</span>
                            <span className="agent-choice-label">{opt.label}</span>
                            {opt.recommended && <span className="agent-choice-badge">Đề xuất</span>}
                            <span className="agent-choice-check" aria-hidden="true">
                                {isSelected && <Check size={13} />}
                            </span>
                        </button>
                    );
                })}

                <div className={`agent-choice-other${useCustom ? " is-selected" : ""}`}>
                    <button
                        type="button"
                        className="agent-choice-other-toggle"
                        onClick={() => {
                            if (prompt.multi) {
                                setUseCustom((v) => !v);
                            } else {
                                setSelected([]);
                                setUseCustom(true);
                            }
                        }}
                        disabled={disabled}
                    >
                        <span className="agent-choice-num">{prompt.options.length + 1}</span>
                        <span>Khác</span>
                    </button>
                    <input
                        type="text"
                        value={customText}
                        placeholder="Nhập lựa chọn hoặc yêu cầu khác của bạn…"
                        disabled={disabled}
                        onFocus={() => {
                            if (!prompt.multi) setSelected([]);
                            setUseCustom(true);
                        }}
                        onChange={(event) => {
                            if (!prompt.multi) setSelected([]);
                            setUseCustom(true);
                            setCustomText(event.target.value);
                        }}
                        onKeyDown={(event) => {
                            if (event.key === "Enter") {
                                event.preventDefault();
                                handleSubmit();
                            }
                        }}
                    />
                </div>
            </div>

            <footer className="agent-choice-foot">
                {prompt.multi && (
                    <button
                        type="button"
                        className="agent-choice-select-all"
                        onClick={() => setSelected(allSelected ? [] : prompt.options.map((opt) => opt.value))}
                        disabled={disabled}
                    >
                        {allSelected ? "Bỏ chọn tất cả" : "Chọn tất cả"}
                    </button>
                )}
                <button type="button" className="agent-choice-skip" onClick={onDismiss} disabled={disabled}>
                    Bỏ qua
                </button>
                <button
                    type="button"
                    className="agent-choice-submit"
                    onClick={handleSubmit}
                    disabled={!canSubmit}
                >
                    Gửi lựa chọn <ArrowRight size={14} />
                </button>
            </footer>
        </section>
    );
}
