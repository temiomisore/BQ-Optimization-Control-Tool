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

export interface StateCompareItem {
  label: string;
  value: unknown;
  badge?: string;
  badgeTone?: "emerald" | "sky" | "amber";
  muted?: boolean;
}

const KEY_ORDER: Record<string, number> = {
  table: 1,
  dataset: 2,
  reservation: 3,
  billing_model: 4,
  partition_granularity: 5,
  provisioned_capacity: 6,
  "30d_bytes_scanned": 7,
  observed_demand: 8,
  commitment_coverage: 9,
  already_on_reservation: 10,
  current_monthly_bill: 11,
  cost_driver: 12,
  status: 20,
  recommended_option_1: 21,
  recommended_option_2: 22,
  projected_net_savings: 23,
  net_savings: 24,
  fallback_option_1: 25,
  fallback_option_1_stay_on_slots: 26,
  fallback_option_2: 27,
  fallback_option_2_stay_on_slots: 28,
};

function Value({ v, tone }: { v: unknown; tone: "rose" | "emerald" | "muted" }) {
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
  const strong =
    tone === "emerald"
      ? "text-emerald-600 dark:text-emerald-300"
      : tone === "rose"
        ? "text-rose-600 dark:text-rose-300"
        : "text-zinc-600 dark:text-zinc-400";

  // Top-level cost formula: "A + B = $Total / month" (never match " = $" nested inside parentheses)
  const eq = s.lastIndexOf(" = ");
  const rhs = eq > 0 ? s.slice(eq + 3).trim() : "";
  if (eq > 0 && /^\$[\d,.]+(?:\s*\/\s*[A-Za-z]+)?$/.test(rhs)) {
    const lhs = s.slice(0, eq);
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
          <ArrowRight className="h-3.5 w-3.5" /> {rhs}
        </div>
      </div>
    );
  }

  const [main, note] = splitNote(s);
  const looksMoney = tone !== "muted" && /^\s*\$[\d,.]/.test(main);
  return (
    <div className="mt-0.5">
      <div
        className={cn(
          tone === "muted" ? "text-xs leading-snug text-zinc-600 dark:text-zinc-400" : "text-sm leading-snug",
          looksMoney ? cn("font-mono font-semibold", strong) : tone === "muted" ? "" : "text-zinc-800 dark:text-zinc-200",
        )}
      >
        <Rich text={main} />
      </div>
      {note && <div className="mt-0.5 text-xs leading-snug text-zinc-500"><Rich text={note} /></div>}
    </div>
  );
}

function badgeClass(tone?: "emerald" | "sky" | "amber") {
  if (tone === "sky") return "bg-sky-500/15 text-sky-600 dark:text-sky-300";
  if (tone === "amber") return "bg-amber-500/15 text-amber-600 dark:text-amber-300";
  return "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300";
}

function StateList({ obj, tone }: { obj: unknown; tone: "rose" | "emerald" }) {
  const dot = tone === "emerald" ? "text-emerald-500" : "text-rose-500";
  if (!obj || typeof obj !== "object") {
    return <p className="text-sm text-zinc-600 dark:text-zinc-300">{String(obj ?? "—")}</p>;
  }
  if (Array.isArray(obj)) {
    const items = obj as StateCompareItem[];
    return (
      <ul className="space-y-3">
        {items.map((item, idx) => {
          const itemTone = item.muted ? "muted" : tone;
          return (
            <li
              key={`${item.label}-${idx}`}
              className={cn(
                "flex gap-2.5",
                item.muted && idx > 0 && "border-t border-zinc-200/80 pt-2.5 dark:border-white/10",
              )}
            >
              <CircleDot className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", item.muted ? "text-zinc-400 dark:text-zinc-500" : dot)} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">{item.label}</span>
                  {item.badge && (
                    <span className={cn("rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider", badgeClass(item.badgeTone))}>
                      {item.badge}
                    </span>
                  )}
                </div>
                <Value v={item.value} tone={itemTone} />
              </div>
            </li>
          );
        })}
      </ul>
    );
  }
  const entries = Object.entries(obj as Record<string, unknown>).sort(
    ([a], [b]) => (KEY_ORDER[a] ?? 999) - (KEY_ORDER[b] ?? 999),
  );
  return (
    <ul className="space-y-3">
      {entries.map(([k, v]) => {
        const isFallback = k.startsWith("fallback_");
        return (
          <li
            key={k}
            className={cn("flex gap-2.5", isFallback && "border-t border-zinc-200/80 pt-2.5 dark:border-white/10")}
          >
            <CircleDot className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", isFallback ? "text-zinc-400 dark:text-zinc-500" : dot)} />
            <div className="min-w-0 flex-1">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">{label(k)}</div>
              <Value v={v} tone={isFallback ? "muted" : tone} />
            </div>
          </li>
        );
      })}
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
