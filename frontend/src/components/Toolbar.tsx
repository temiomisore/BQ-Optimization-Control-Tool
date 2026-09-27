import { Search, X } from "lucide-react";
import type { Dashboard } from "@/lib/api";
import { NativeSelect } from "./ui";
import { cn, CLASS_META } from "@/lib/utils";

export interface Filters {
  q: string;
  cls: number | 0;
  minSavings: number;
  project: string;
  dataset: string;
  director: string;
  confidence: "" | "high" | "med" | "twoperson";
  sort: "savings_desc" | "savings_asc" | "confidence_desc" | "class_asc" | "table_asc";
}

export const DEFAULT_FILTERS: Filters = {
  q: "", cls: 0, minSavings: 0, project: "", dataset: "", director: "", confidence: "", sort: "savings_desc",
};

export function Toolbar({
  data,
  f,
  setF,
  shown,
  total,
}: {
  data: Dashboard;
  f: Filters;
  setF: (p: Partial<Filters>) => void;
  shown: number;
  total: number;
}) {
  const counts = data.kpis.class_counts || {};
  const dirty = JSON.stringify(f) !== JSON.stringify(DEFAULT_FILTERS);
  return (
    <div className="panel relative z-30 flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[240px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
          <input
            className="input pl-9"
            placeholder="Search tables, datasets, rules, SQL, IDs…"
            value={f.q}
            onChange={(e) => setF({ q: e.target.value })}
          />
        </div>
        <div className="flex flex-wrap gap-1 rounded-lg bg-zinc-100 p-1 dark:bg-[#0c0c0f]">
          {[0, 1, 2, 3, 4].map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setF({ cls: c })}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all",
                f.cls === c
                  ? "bg-white text-sky-600 shadow-sm dark:bg-zinc-800 dark:text-sky-300"
                  : "text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200",
              )}
            >
              {c ? <span className={cn("h-1.5 w-1.5 rounded-full", CLASS_META[c].dot)} /> : null}
              {c ? `Class ${c}` : "All"}
              <span className="font-mono text-[10px] text-zinc-400">{c ? counts[c] || 0 : total}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <NativeSelect label="Min value" value={String(f.minSavings)} onChange={(v) => setF({ minSavings: Number(v) })}>
          <option value="0">Any savings</option>
          <option value="50">$50+ / mo</option>
          <option value="100">$100+ / mo</option>
          <option value="500">$500+ / mo</option>
          <option value="1000">$1,000+ / mo</option>
        </NativeSelect>
        <NativeSelect label="Project" value={f.project} onChange={(v) => setF({ project: v })}>
          <option value="">All projects ({data.projects.length})</option>
          {data.projects.map((p) => <option key={p} value={p}>{p}</option>)}
        </NativeSelect>
        <NativeSelect label="Dataset" value={f.dataset} onChange={(v) => setF({ dataset: v })}>
          <option value="">All datasets ({data.datasets.length})</option>
          {data.datasets.map((d) => <option key={d} value={d}>{d}</option>)}
        </NativeSelect>
        <NativeSelect label="Owner" value={f.director} onChange={(v) => setF({ director: v })}>
          <option value="">All directors ({data.directors.length})</option>
          {data.directors.map((d) => <option key={d} value={d}>{d}</option>)}
        </NativeSelect>
        <NativeSelect label="Confidence" value={f.confidence} onChange={(v) => setF({ confidence: v as Filters["confidence"] })}>
          <option value="">All levels</option>
          <option value="high">High (≥ 80%)</option>
          <option value="med">Medium (50–79%)</option>
          <option value="twoperson">Needs 2-person sign-off</option>
        </NativeSelect>
        <NativeSelect label="Sort by" value={f.sort} onChange={(v) => setF({ sort: v as Filters["sort"] })}>
          <option value="savings_desc">Savings: high → low</option>
          <option value="savings_asc">Savings: low → high</option>
          <option value="confidence_desc">Confidence: high → low</option>
          <option value="class_asc">Class 1 → 4</option>
          <option value="table_asc">Dataset & table A → Z</option>
        </NativeSelect>
      </div>

      <div className="flex items-center justify-between text-xs text-zinc-500">
        <span>
          Showing <b className="font-mono text-zinc-800 dark:text-zinc-200">{shown}</b> of{" "}
          <b className="font-mono text-zinc-800 dark:text-zinc-200">{total}</b> recommendations
        </span>
        {dirty && (
          <button type="button" onClick={() => setF(DEFAULT_FILTERS)} className="flex items-center gap-1 text-sky-500 hover:text-sky-400">
            <X className="h-3.5 w-3.5" /> Clear filters
          </button>
        )}
      </div>
    </div>
  );
}
