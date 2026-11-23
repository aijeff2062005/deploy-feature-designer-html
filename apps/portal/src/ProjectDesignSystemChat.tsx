import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowUp, Check, CircleCheck, ExternalLink, LoaderCircle, MessageCircle, Sparkles, X } from "lucide-react";
import { api, type ProjectStyleOption } from "./api";

type ChatMessage = { role: "agent" | "user"; text: string };
type PreviewSet = { runId: string; options: ProjectStyleOption[] };

function ProjectPreviewFrame({ option }: { option: ProjectStyleOption }) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0.6);
  const width = Math.max(240, option.preview_width || 390);
  const height = Math.max(320, option.preview_height || 844);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(([entry]) => setScale(Math.min(1, entry.contentRect.width / width)));
    observer.observe(frame);
    return () => observer.disconnect();
  }, [width]);

  return <div className="project-ds-preview-frame" ref={frameRef} style={{ height: height * scale }}>
    <iframe title={`UI template ${option.name}`} srcDoc={previewDocument(option)} sandbox="" loading="lazy" style={{ width, height, transform: `scale(${scale})` }} />
  </div>;
}

function previewOptions(payload: Record<string, unknown> | undefined): ProjectStyleOption[] {
  const options = payload?.options;
  if (!Array.isArray(options)) return [];
  return options.filter((item): item is ProjectStyleOption =>
    !!item && typeof item === "object" && typeof (item as ProjectStyleOption).html === "string" && typeof (item as ProjectStyleOption).compiled_css === "string",
  );
}

function projectContextMessage(projectName: string, description: string): string {
  return `Thông tin project\nTên project: ${projectName}\nMô tả: ${description.trim() || "Chưa có mô tả."}`;
}

function previewDocument(option: ProjectStyleOption): string {
  const policy = "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'";
  const css = (option.compiled_css || "").replace(/<\/style/gi, "<\\/style");
  const frameCss = "html,body{margin:0!important;overflow:hidden!important;scrollbar-width:none!important}html::-webkit-scrollbar,body::-webkit-scrollbar{display:none!important}";
  return `<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="${policy}"><style>${css}\n${frameCss}</style></head><body>${option.html}</body></html>`;
}

function replySuggestions(text: string): { message: string; choices: string[] } {
  const match = text.match(/\n?Gợi ý trả lời:\s*(.+)$/im);
  if (!match) return { message: text, choices: [] };
  return {
    message: text.slice(0, match.index).trim(),
    choices: match[1].split(/\s*\|\s*/).map((item) => item.trim()).filter(Boolean).slice(0, 4),
  };
}

export function ProjectDesignSystemChat({
  projectId, projectName, description, designSystemId, figmaContext = false, creationFlow = false, onClose, onOpenDesigner,
}: {
  projectId: string; projectName: string; description: string;
  designSystemId?: string; figmaContext?: boolean;
  creationFlow?: boolean;
  onClose: () => void; onOpenDesigner: (sessionId: string) => void;
}) {
  const [sessionId, setSessionId] = useState("");
  const sessionRef = useRef("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(true);
  const [savingKey, setSavingKey] = useState("");
  const [error, setError] = useState("");
  const [previewSet, setPreviewSet] = useState<PreviewSet | null>(null);
  const [saved, setSaved] = useState<{ version: number; name: string } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }); }, [messages, previewSet, busy]);

  const waitForRun = async (start: Awaited<ReturnType<typeof api.getDesignerRun>>) => {
    let run = start;
    for (let attempt = 0; attempt < 120 && (run.status === "queued" || run.status === "running"); attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
      run = await api.getDesignerRun(start.id);
    }
    if (run.status === "queued" || run.status === "running") {
      throw new Error("Agent đang xử lý lâu hơn dự kiến. Hãy thử gửi lại sau ít phút.");
    }
    return run;
  };

  const loadRunPreview = async (runId: string) => {
    const events = await api.getDesignerRunEvents(runId);
    const styleEvent = [...events].reverse().find((event) => event.type === "style.previews");
    const options = previewOptions(styleEvent?.payload);
    return options.length ? { runId, options } : null;
  };

  const submitAgentMessage = async (content: string, displayText: string) => {
    const sid = sessionRef.current;
    if (!sid) return;
    setBusy(true);
    setError("");
    setMessages((items) => [...items, { role: "user", text: displayText }]);
    try {
      const created = await api.sendDesignerMessage(sid, content);
      const run = await waitForRun(created);
      if (run.status === "failed" || run.status === "blocked") {
        throw new Error(run.error?.message || "Agent chưa xử lý được câu trả lời này.");
      }
      const agentText = run.assistant_message?.trim() || "Mình đã nhận thông tin. Bạn có thể nói thêm điều muốn ưu tiên cho giao diện.";
      setMessages((items) => [...items, { role: "agent", text: agentText }]);
      const restoredPreview = await loadRunPreview(created.id);
      if (restoredPreview) setPreviewSet(restoredPreview);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không gửi được tin nhắn.");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    const begin = async () => {
      try {
        const [sessions, wiki] = await Promise.all([
          api.designerSessions(projectId),
          api.projectDesignWiki(projectId).catch(() => ({ current: null })),
        ]);
        const projectSessions = sessions
          .filter((item) => item.source === "project-design-system")
          .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
        const savedSession = wiki.current?.source_session_id;
        const existing = projectSessions.find((item) => item.id === savedSession) || projectSessions[0];
        const session = existing || await api.createProjectDesignSession(projectId, projectName, description, designSystemId);
        if (!mounted) return;
        sessionRef.current = session.id;
        setSessionId(session.id);
        if (existing) {
          const runs = (await api.designerRuns(existing.id)).sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));
          const restored: ChatMessage[] = [];
          let latestPreview: PreviewSet | null = null;
          for (const [index, item] of runs.entries()) {
            const run = item.status === "queued" || item.status === "running" ? await waitForRun(item) : item;
            if (run.status === "failed" || run.status === "blocked") continue;
            const firstRunLabel = index === 0
              ? projectContextMessage(projectName, description)
              : (run.prompt || "");
            if (firstRunLabel) restored.push({ role: "user", text: firstRunLabel });
            if (run.assistant_message?.trim()) restored.push({ role: "agent", text: run.assistant_message.trim() });
            const runPreview = await loadRunPreview(run.id);
            if (runPreview) latestPreview = runPreview;
          }
          if (!mounted) return;
          setMessages(restored);
          if (latestPreview) setPreviewSet(latestPreview);
          if (wiki.current?.source_session_id === existing.id) {
            setSaved({ version: wiki.current.version, name: wiki.current.baseline?.contract?.name || "Design System" });
          }
          setBusy(false);
          return;
        }
        const prompt = [
          `Mình muốn tạo Design System dùng chung cho toàn bộ dự án “${projectName}”.`,
          description.trim() ? `Mô tả dự án hiện có: ${description.trim()}` : "Dự án chưa có mô tả.",
          ...(figmaContext ? ["Project đã nạp một file Figma và trích xuất Design System. File chưa có màn hình đủ điều kiện làm template dùng chung. Hãy kế thừa token/component đã nhập; hỏi thêm phần còn thiếu cần thiết rồi dựng các mẫu UI để người dùng chọn và lưu template vào Project Wiki."] : []),
          "Hãy bắt đầu từ thông tin đã biết. Nếu chưa đủ để hiểu sản phẩm, người dùng và mục tiêu chính, hãy hỏi mình câu cần thiết tiếp theo. Đừng tự suy đoán chỉ từ tên dự án.",
        ].join("\n\n");
        await submitAgentMessage(prompt, projectContextMessage(projectName, description));
      } catch (cause) {
        if (mounted) { setError(cause instanceof Error ? cause.message : "Không mở được Design System Agent."); setBusy(false); }
      }
    };
    void begin();
    return () => { mounted = false; };
    // Resume the existing project-wide Q&A session after reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const send = (value = draft) => {
    const text = value.trim();
    if (!text || busy) return;
    setDraft("");
    void submitAgentMessage(text, text);
  };

  const approve = async (option: ProjectStyleOption) => {
    if (!sessionId || !previewSet || savingKey) return;
    setSavingKey(option.key);
    setError("");
    try {
      const result = await api.approveProjectDesignTemplate(projectId, sessionId, previewSet.runId, option.key);
      setSaved({ version: result.version, name: result.name });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không lưu được template vào Project Wiki.");
    } finally {
      setSavingKey("");
    }
  };

  return (
    <div className={`project-ds-overlay${creationFlow ? " creation-flow" : ""}`} role="dialog" aria-modal="true" aria-label="Chat với Design System Agent">
      <section className="project-ds-chat">
        <header className="project-ds-head">
          <span className="project-ds-agent-icon"><Sparkles size={17} /></span>
          <div><strong>Design System Agent</strong><small>{projectName} · dùng chung toàn project</small></div>
          <button type="button" className="icon-btn" aria-label="Đóng" onClick={onClose}><X size={17} /></button>
        </header>
        <div className="project-ds-context"><MessageCircle size={15} /><span>Agent dùng tên và mô tả project làm ngữ cảnh. Bạn trả lời tự do; Agent sẽ hỏi thêm nếu còn thiếu thông tin.</span></div>
        <div className="project-ds-messages" ref={scrollRef} aria-live="polite">
          {messages.map((message, index) => {
            const parsed = message.role === "agent" ? replySuggestions(message.text) : { message: message.text, choices: [] as string[] };
            const context = message.role === "user" && message.text.startsWith("Thông tin project\n")
              ? message.text.split("\n")
              : null;
            return <div className={`project-ds-message ${message.role}`} key={`${index}-${message.role}`}>
              {message.role === "agent" && <span className="project-ds-avatar"><Sparkles size={13} /></span>}
              <div className={`project-ds-bubble${context ? " project-ds-project-context" : ""}`}>
                {context ? <><small>THÔNG TIN PROJECT</small><strong>{context[1]?.replace("Tên project: ", "")}</strong><p>{context[2]?.replace("Mô tả: ", "")}</p></> : <p>{parsed.message}</p>}
                {parsed.choices.length > 0 && <div className="project-ds-suggestions">{parsed.choices.map((choice) => <button key={choice} type="button" disabled={busy} onClick={() => send(choice)}>{choice}</button>)}</div>}
              </div>
            </div>;
          })}
          {busy && <div className="project-ds-thinking"><span className="project-ds-thinking-icon"><Sparkles size={14} /></span><LoaderCircle className="spin" size={15} /><span>Agent đang xử lý câu trả lời…</span></div>}
          {previewSet && <section className="project-ds-gallery">
            <div className="project-ds-gallery-head"><strong>Ba hướng giao diện cho project</strong><small>Được dựng từ mô tả và phần bạn vừa xác nhận</small></div>
            <div className="project-ds-options">
              {previewSet.options.map((option, index) => <article className="project-ds-option" key={option.key}>
                <div className="project-ds-option-title"><span>{String.fromCharCode(65 + index)}</span><strong>{option.name}</strong>{option.recommended && <em>Gợi ý</em>}</div>
                <p>{option.tagline || option.direction}</p>
                <ProjectPreviewFrame option={option} />
                <button type="button" className="btn primary" disabled={!!savingKey || !!saved} onClick={() => void approve(option)}>
                  {saved?.name === option.name ? <><Check size={15} /> Đã chọn</> : savingKey === option.key ? <><LoaderCircle className="spin" size={15} /> Đang lưu…</> : "Chọn làm chuẩn project"}
                </button>
              </article>)}
            </div>
            <p className="project-ds-gallery-note">Đây là mẫu thị giác dùng chung. Khi vào từng feature, Designer vẫn dựng màn hình theo yêu cầu riêng của feature đó.</p>
          </section>}
          {saved && <div className="project-ds-saved"><CircleCheck size={20} /><div><strong>Đã lưu {saved.name} · Design System v{saved.version}</strong><span>Project Wiki đã có baseline để các feature dùng lại.</span></div></div>}
          {error && <div className="project-ds-error">{error}</div>}
        </div>
        <form className="project-ds-composer" onSubmit={(event: FormEvent) => { event.preventDefault(); send(); }}>
          <textarea value={draft} onChange={(event) => setDraft(event.target.value)} disabled={busy || !!saved} rows={2} placeholder={saved ? "Design System đã lưu vào Project Wiki" : "Trả lời Agent hoặc bổ sung yêu cầu…"} />
          <button className="btn primary" type="submit" disabled={!draft.trim() || busy || !!saved} aria-label="Gửi"><ArrowUp size={17} /></button>
        </form>
        <footer className="project-ds-footer">
          <span>{saved ? `Project Wiki · v${saved.version}` : "Q&A · Agent hỏi theo ngữ cảnh project"}</span>
          <div>
            {sessionId && <button type="button" onClick={() => onOpenDesigner(sessionId)}><ExternalLink size={14} /> Mở Designer</button>}
            {saved && <button type="button" className="done" onClick={onClose}><Check size={14} /> Hoàn tất</button>}
          </div>
        </footer>
      </section>
    </div>
  );
}
