import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, FileCheck2, RotateCcw, Sparkles, Trophy } from "lucide-react";
import type { Receipt } from "@/lib/api";
import { cn, usd } from "@/lib/utils";
import { Button } from "./ui";

/*
 * DEMO ONLY — simulated "Day 14" verification receipts.
 *
 * Real verification needs 14 days of post-change query history (optimizer/verifier.py
 * check()), which can't happen live on stage. For the two demo cards below we show the
 * receipt a customer would get on Day 14, clearly labeled SIMULATED. Nothing is written
 * to BigQuery; predicted values and applied dates still come from the live receipts.
 * Real values populate automatically in v_receipts after the verify window.
 */
export interface SimReceipt {
  realized: number;
  gibBefore: number;
  gibAfter: number;
  p95Before: number;
  p95After: number;
  families: number;
  regressions: number;
}

const DEMO_SIM: Record<string, SimReceipt> = {
  "demo_ecommerce.orders": { realized: 982.5, gibBefore: 48.6, gibAfter: 9.7, p95Before: 14.2, p95After: 4.1, families: 37, regressions: 0 },
  "demo_ecommerce.audit_logs_unpartitioned": { realized: 67.24, gibBefore: 21.3, gibAfter: 0.8, p95Before: 9.8, p95After: 1.6, families: 12, regressions: 0 },
};

const SIM_STATES = ["VERIFYING", "VERIFIED"];
const STORAGE_KEY = "bqopt.demoDay14Receipts";

export const receiptKey = (r: Receipt) => `${r.target_dataset}${r.target_table ? "." + r.target_table : ""}`;

/** Simulated values for a receipt, or null when it isn't one of the demo cards. */
export function simFor(r: Receipt): SimReceipt | null {
  return SIM_STATES.includes(r.state) ? DEMO_SIM[receiptKey(r)] ?? null : null;
}

export function useDemoDay14() {
  const [on, setOn] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
    } catch {
      /* private mode: keep in memory only */
    }
  }, [on]);
  return [on, setOn] as const;
}

const pctDrop = (a: number, b: number) => (a > 0 ? Math.round(((b - a) / a) * 100) : 0);

function issuedAt(applied?: string) {
  if (!applied) return "—";
  const d = new Date(applied);
  if (isNaN(d.getTime())) return "—";
  d.setUTCDate(d.getUTCDate() + 14);
  return `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/** Call-to-action shown while the demo cards are still Verifying. */
export function SimulateDay14Banner({ count, onSimulate }: { count: number; onSimulate: () => void }) {
  return (
    <div className="panel mb-4 flex flex-wrap items-center justify-between gap-3 p-4">
      <div className="text-sm">
        <div className="font-medium">
          {count} change{count === 1 ? "" : "s"} in the 14-day verification window
        </div>
        <div className="text-xs text-zinc-500">
          The optimizer compares query cost before vs. after once 14 days of history exist. Preview the Day-14 receipt
          (simulated for demo).
        </div>
      </div>
      <Button size="sm" onClick={onSimulate}>
        <Sparkles className="h-3.5 w-3.5" />
        Simulate Day-14 verification
      </Button>
    </div>
  );
}

/** "CFO Proof Receipt — Day 14" panel. */
export function CfoProofReceipt({ receipts, onReset }: { receipts: Receipt[]; onReset: () => void }) {
  const rows = receipts.map((r) => ({ r, s: simFor(r)! })).filter((x) => x.s);
  const predicted = rows.reduce((t, x) => t + Number(x.r.predicted_usd || 0), 0);
  const realized = rows.reduce((t, x) => t + x.s.realized, 0);
  const regressions = rows.reduce((t, x) => t + x.s.regressions, 0);
  const accuracy = predicted > 0 ? Math.round((realized / predicted) * 100) : 0;

  const kpis = [
    { label: "Predicted", value: `${usd(predicted)}/mo` },
    { label: "Realized (sim)", value: `${usd(realized)}/mo`, tone: "text-emerald-400" },
    { label: "Accuracy", value: `${accuracy}% of prediction` },
    { label: "Annualized realized", value: `${usd(realized * 12)}/yr`, tone: "text-emerald-400" },
    { label: "Regressions", value: String(regressions) },
  ];

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="panel mb-4 overflow-x-auto p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <FileCheck2 className="h-4 w-4 text-sky-400" />
          <span className="text-sm font-semibold">CFO Proof Receipt — Day 14</span>
          <span className="inline-flex items-center gap-1 rounded-md bg-amber-500/15 px-2 py-0.5 font-mono text-[11px] font-semibold text-amber-500">
            <AlertTriangle className="h-3 w-3" />
            SIMULATED FOR DEMO
          </span>
        </div>
        <Button size="sm" variant="ghost" onClick={onReset}>
          <RotateCcw className="h-3.5 w-3.5" />
          Back to live view
        </Button>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
        {kpis.map((k) => (
          <div key={k.label} className="rounded-lg border border-zinc-200 px-3 py-2 dark:border-ink-800">
            <div className="label">{k.label}</div>
            <div className={cn("font-mono text-sm font-semibold", k.tone)}>{k.value}</div>
          </div>
        ))}
      </div>

      <p className="mb-2 flex items-center gap-1 text-[11px] text-amber-500">
        <AlertTriangle className="h-3 w-3" />
        Simulated Day-14 values for demo purposes. Real values populate automatically in v_receipts after the 14-day
        verify window.
      </p>

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-zinc-200 text-left dark:border-ink-800">
            {[
              "Target table",
              "Predicted / mo",
              "Realized / mo (sim)",
              "Realized ÷ predicted",
              "Avg GiB / query",
              "p95 latency (s)",
              "Query families",
              "Regressions",
              "Verdict",
              "State",
              "Receipt issued (sim)",
            ].map((h) => (
              <th key={h} className="label whitespace-nowrap px-3 py-2">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ r, s }) => {
            const pred = Number(r.predicted_usd || 0);
            const ratio = pred > 0 ? s.realized / pred : 0;
            const beat = ratio >= 1;
            return (
              <tr key={r.change_set_id} className="border-b border-zinc-100 dark:border-ink-800/60">
                <td className="whitespace-nowrap px-3 py-2.5 font-medium">{receiptKey(r)}</td>
                <td className="px-3 py-2.5 font-mono">{usd(pred)}</td>
                <td className="bg-emerald-500/10 px-3 py-2.5 font-mono font-semibold text-emerald-400">{usd(s.realized)}</td>
                <td className="bg-emerald-500/10 px-3 py-2.5 font-mono">{ratio.toFixed(2)}×</td>
                <td className="whitespace-nowrap px-3 py-2.5 font-mono text-xs">
                  {s.gibBefore} → {s.gibAfter} <span className="text-emerald-400">({pctDrop(s.gibBefore, s.gibAfter)}%)</span>
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 font-mono text-xs">
                  {s.p95Before} → {s.p95After}
                </td>
                <td className="px-3 py-2.5 font-mono">{s.families}</td>
                <td className="px-3 py-2.5 font-mono">{s.regressions}</td>
                <td className="px-3 py-2.5">
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-0.5 text-[11px] font-semibold",
                      beat ? "bg-emerald-500/15 text-emerald-400" : "bg-sky-500/15 text-sky-400",
                    )}
                  >
                    {beat ? <Trophy className="h-3 w-3" /> : <CheckCircle2 className="h-3 w-3" />}
                    {beat ? "Beat prediction" : "Within ±15% band"}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-xs font-semibold text-emerald-400">VERIFIED</td>
                <td className="whitespace-nowrap px-3 py-2.5 font-mono text-xs text-zinc-500">{issuedAt(r.applied_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </motion.div>
  );
}
