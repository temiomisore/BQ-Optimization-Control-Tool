import { ArrowRight, CircleDot, History, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

/** "30d_bytes_scanned" -> "30d bytes scanned", "option_1_steady_24x7" -> "Option 1 steady 24x7" */
function label(k: string) {
  const s = k.replace(/_/g, " ").replace(/\s+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Split "main (note)" into a bold main value and a muted note. */
function splitNote(v: string): [string, string | null] {
  const m = v.match(/^(.*?)\s*\((.+)\)\s*$/);
  return m && m[1] ? [m[1], m[2]] : [v, null];
}

/** Render `backtick` segments as inline code; everything else as plain text (no HTML injection). */
export function Rich({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("`") && p.endsWith("`") && p.length > 2 ? (
          <code key={i} className="[overflow-wrap:anywhere] rounded bg-zinc-100 px-1 py-0.5 font-mono text-[12px] text-sky-700 dark:bg-white/5 dark:text-sky-300">{p.slice(1, -1)}</code>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

function Value({ v, tone }: { v: unknown; tone: "rose" | "emerald" }) {
  // nested objects / arrays -> sub-bullets
  if (Array.isArray(v)) {
    return (
      <ul className="mt-1 space-y-1 border-l border-zinc-200 pl-3 dark:border-white/10">
        {v.map((x, i) => (
          <li key={i} className="text-[13px] text-zinc-700 dark:text-zinc-300">{typeof x === "object" ? JSON.stringify(x) : String(x)}</li>
        ))}
      </ul>
    );
  }
  if (v && typeof v === "object") {
    return (
      <ul className="mt-1 space-y-1.5 border-l border-zinc-200 pl-3 dark:border-white/10">
        {Object.entries(v as Record<string, unknown>).map(([k, x]) => (
          <li key={k} className="text-[13px]">
            <span className="text-zinc-500">{label(k)}: </span>
            <span className="text-zinc-800 dark:text-zinc-200">{typeof x === "object" ? JSON.stringify(x) : String(x)}</span>
          </li>
        ))}
      </ul>
    );
  }

  const s = String(v ?? "—");
  const strong = tone === "emerald" ? "text-emerald-600 dark:text-emerald-300" : "text-rose-600 dark:text-rose-300";

  // cost formula: "A + B = Total"
  const eq = s.lastIndexOf(" = ");
  if (eq > 0 && /\$\d/.test(s.slice(eq))) {
    const lhs = s.slice(0, eq);
    const total = s.slice(eq + 3);
    const parts = lhs.split(/\s\+\s/);
    return (
      <div className="mt-1 space-y-1">
        <ul className="space-y-0.5">
          {parts.map((p, i) => (
            <li key={i} className="flex gap-2 text-[13px] text-zinc-600 dark:text-zinc-400">
              <span className="text-zinc-400">{i === 0 ? "•" : "+"}</span>
              <span><Rich text={p} /></span>
            </li>
          ))}
        </ul>
        <div className={cn("flex items-center gap-1.5 font-mono text-sm font-semibold", strong)}>
          <ArrowRight className="h-3.5 w-3.5" /> {total}
        </div>
      </div>
    );
  }

  const [main, note] = splitNote(s);
  const looksMoney = /\$\d/.test(main);
  return (
    <div className="mt-0.5">
      <div className={cn("text-sm leading-snug", looksMoney ? cn("font-mono font-semibold", strong) : "text-zinc-800 dark:text-zinc-200")}><Rich text={main} /></div>
      {note && <div className="mt-0.5 text-xs leading-snug text-zinc-500"><Rich text={note} /></div>}
    </div>
  );
}

function StateList({ obj, tone }: { obj: unknown; tone: "rose" | "emerald" }) {
  const dot = tone === "emerald" ? "text-emerald-500" : "text-rose-500";
  if (!obj || typeof obj !== "object") {
    return <p className="text-sm text-zinc-600 dark:text-zinc-300">{String(obj ?? "—")}</p>;
  }
  return (
    <ul className="space-y-3">
      {Object.entries(obj as Record<string, unknown>).map(([k, v]) => (
        <li key={k} className="flex gap-2.5">
          <CircleDot className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", dot)} />
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">{label(k)}</div>
            <Value v={v} tone={tone} />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function StateCompare({ before, after }: { before: unknown; after: unknown }) {
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <div className="rounded-xl border border-rose-500/25 bg-rose-500/[0.04] p-4">
        <div className="mb-3 flex items-center gap-2 border-b border-rose-500/15 pb-2">
          <History className="h-4 w-4 text-rose-500 dark:text-rose-400" />
          <span className="text-xs font-semibold uppercase tracking-wider text-rose-600 dark:text-rose-400">Before · today</span>
        </div>
        <StateList obj={before} tone="rose" />
      </div>
      <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/[0.04] p-4">
        <div className="mb-3 flex items-center gap-2 border-b border-emerald-500/15 pb-2">
          <Sparkles className="h-4 w-4 text-emerald-500 dark:text-emerald-400" />
          <span className="text-xs font-semibold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">After · proposed</span>
        </div>
        <StateList obj={after} tone="emerald" />
      </div>
    </div>
  );
}
