import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Loader2, RotateCcw, Send, Sparkles, Trash2, X } from "lucide-react";
import { askAssist, type ChatTurn } from "@/lib/api";
import { useReviewer } from "@/lib/store";
import { cn } from "@/lib/utils";
import { ErrorBoundary } from "./ErrorBoundary";

export const ASSIST_NAME = "Gemini FinOps Assist";

const CHIPS = [
  "Will W-02 break our Service Accounts or ETL jobs?",
  "Compare W-01 Option 1 vs Option 2 reservation pricing",
  "How do our 3 Class 4 SQL engines work?",
  "Summarize our Top 5 ROI recommendations",
];

/** Tiny, safe markdown → HTML (escape first, then bold/code/headings/lists). Never throws. */
function md(src: unknown): string {
  try {
    const esc = String(src ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    let html = "";
    let inList = false;
    let inCode = false;
    for (const raw of esc.split("\n")) {
      if (raw.trim().startsWith("```")) {
        html += inCode ? "</pre>" : "<pre>";
        inCode = !inCode;
        continue;
      }
      if (inCode) { html += raw + "\n"; continue; }
      let l = raw
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/`([^`]+)`/g, "<code>$1</code>");
      const li = l.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
      if (li) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += `<li>${li[1]}</li>`;
        continue;
      }
      if (inList) { html += "</ul>"; inList = false; }
      const h = l.match(/^#{1,4}\s+(.*)$/);
      if (h) l = `<h4>${h[1]}</h4>`;
      else if (l.trim()) l = `<p>${l}</p>`;
      html += l;
    }
    if (inList) html += "</ul>";
    if (inCode) html += "</pre>";
    return html;
  } catch {
    return String(src ?? "").replace(/</g, "&lt;");
  }
}

function Bubble({ t }: { t: ChatTurn }) {
  const user = t.role === "user";
  return (
    <div className={cn("flex", user ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[88%] rounded-2xl px-3.5 py-2.5 text-sm",
          user
            ? "rounded-br-sm bg-sky-500 text-zinc-950"
            : "rounded-bl-sm border border-zinc-200 bg-white text-zinc-800 dark:border-white/10 dark:bg-zinc-900/70 dark:text-zinc-200",
        )}
      >
        {user ? (
          <span>{String(t.text ?? "")}</span>
        ) : (
          // Wrapper div owns no React children, so DOM edits (e.g. browser translate) can't desync React.
          <div className="md" dangerouslySetInnerHTML={{ __html: md(t.text) }} />
        )}
        {!user && t.model && (
          <div className="mt-2 font-mono text-[10px] uppercase tracking-wider text-zinc-500">{String(t.model)}</div>
        )}
      </div>
    </div>
  );
}

export function GeminiAssistDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  // The server answers from the same viewer-scoped queue as the dashboard.
  const { email: reviewerEmail } = useReviewer();
  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); } catch { /* ignore */ }
  }, [history, busy]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const ask = async (question: string) => {
    const text = question.trim();
    if (!text || busy) return;
    const prior = history;
    setHistory([...prior, { role: "user", text }]);
    setQ("");
    setBusy(true);
    try {
      const r = await askAssist(text, prior, reviewerEmail || undefined);
      const answer = typeof r?.answer === "string" && r.answer.trim() ? r.answer : "I couldn't produce an answer — please try rephrasing.";
      setHistory((h) => [...h, { role: "ai", text: answer, model: typeof r?.model === "string" ? r.model : undefined }]);
    } catch (e) {
      setHistory((h) => [...h, { role: "ai", text: `⚠️ ${(e as Error)?.message || "Request failed"}`, model: "error" }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div key="assist-overlay" className="fixed inset-0 z-40" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <div className="absolute inset-0 bg-zinc-950/40 backdrop-blur-[2px]" onClick={onClose} />
          <motion.aside
            translate="no"
            className="glass notranslate absolute right-0 top-0 z-50 flex h-full w-full max-w-md flex-col border-l border-zinc-200 dark:border-white/10"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 28, stiffness: 260 }}
          >
            <div className="flex items-center gap-3 border-b border-zinc-200 p-4 dark:border-white/10">
              <div className="rounded-lg bg-gradient-to-br from-blue-500 via-violet-500 to-rose-400 p-2 shadow-glow">
                <Sparkles className="h-4 w-4 text-white" />
              </div>
              <div className="flex-1">
                <div className="font-semibold">{ASSIST_NAME}</div>
                <div className="text-[11px] text-zinc-500">Powered by Gemini on Vertex AI · grounded in your live queue</div>
              </div>
              {history.length > 0 && (
                <button onClick={() => setHistory([])} className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-white/5" title="Clear chat">
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
              <button onClick={onClose} className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-white/5" aria-label="Close">
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="flex-1 space-y-4 overflow-y-auto p-4">
              <ErrorBoundary
                where="assist-messages"
                fallback={(err, reset) => (
                  <div className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-300">
                    <div>Couldn't display that answer ({err.message}). It's been logged.</div>
                    <button className="inline-flex items-center gap-1 underline" onClick={() => { setHistory([]); reset(); }}>
                      <RotateCcw className="h-3 w-3" /> Start a new chat
                    </button>
                  </div>
                )}
              >
                {history.length === 0 && (
                  <div className="space-y-3 pt-6 text-center">
                    <Sparkles className="mx-auto h-10 w-10 text-sky-400/70" />
                    <div className="text-sm text-zinc-500 dark:text-zinc-400">Ask Gemini about savings, risk, or how any recommendation works.</div>
                  </div>
                )}
                {history.map((t, i) => <Bubble key={i} t={t} />)}
                {busy && (
                  <div className="flex items-center gap-2 text-xs text-zinc-500">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Gemini is thinking…
                  </div>
                )}
              </ErrorBoundary>
              <div ref={end} />
            </div>

            <div className="space-y-3 border-t border-zinc-200 p-4 dark:border-white/10">
              <div className="flex flex-wrap gap-1.5">
                {CHIPS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    disabled={busy}
                    onClick={() => ask(c)}
                    className="rounded-full border border-zinc-300 px-2.5 py-1 text-[11px] text-zinc-500 transition-colors hover:border-sky-500/40 hover:text-sky-500 disabled:opacity-50 dark:border-white/10 dark:text-zinc-400 dark:hover:text-sky-300"
                  >
                    {c}
                  </button>
                ))}
              </div>
              <form
                className="flex gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  ask(q);
                }}
              >
                <input className="input flex-1" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask Gemini about your BigQuery costs…" />
                <button
                  type="submit"
                  disabled={busy || !q.trim()}
                  className="rounded-lg bg-gradient-to-r from-sky-500 to-cyan-400 px-3 text-zinc-950 transition-opacity disabled:opacity-40"
                  aria-label="Send"
                >
                  <Send className="h-4 w-4" />
                </button>
              </form>
            </div>
          </motion.aside>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
