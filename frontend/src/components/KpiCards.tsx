import { motion } from "framer-motion";
import { Wallet, ListChecks, ShieldAlert, History, ArrowUpRight } from "lucide-react";
import type { Dashboard } from "@/lib/api";
import { AnimatedNumber, Skeleton } from "./ui";
import { cn, usd } from "@/lib/utils";

export type TabKey = "queue" | "finops" | "handoffs" | "blocked" | "regressed" | "rolled_back" | "receipts";

export function KpiCards({ data, onTab }: { data?: Dashboard; onTab: (t: TabKey) => void }) {
  if (!data) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="panel p-5">
            <Skeleton className="mb-4 h-3 w-24" />
            <Skeleton className="h-8 w-36" />
          </div>
        ))}
      </div>
    );
  }
  const k = data.kpis;
  const overlap = Number(k.overlap_removed_usd || 0);
  const demoCards = Number(k.demo_floor_cards || 0);
  const legacyCards = Number(k.legacy_estimate_cards || 0);
  const spend = k.compute_spend_30d_usd;
  const savingsFoot = [
    `${usd(k.annual_savings)} / year`,
    overlap > 0 ? `${usd(overlap)} overlap removed (sum of cards ${usd(k.monthly_savings_gross_sum)})` : null,
    k.headline_capped_at_spend
      ? spend != null ? `capped at actual 30-day query bill (${usd(spend)})` : "capped at actual 30-day query bill"
      : null,
    legacyCards > 0 ? `${legacyCards} legacy estimate${legacyCards > 1 ? "s" : ""}` : null,
  ].filter(Boolean).join(" · ");
  const items = [
    {
      title: "Net monthly savings",
      icon: Wallet,
      value: k.monthly_savings,
      fmt: (n: number) => usd(n),
      foot: savingsFoot,
      pill: demoCards > 0 ? `${demoCards} demo floor${demoCards > 1 ? "s" : ""}` : "billing-aware",
      tone: demoCards > 0 ? "amber" : "emerald",
      onClick: undefined as undefined | (() => void),
      tip: "Each card is priced the way its jobs are billed: on-demand jobs by bytes x $/TiB, reservation jobs by slot-hours x the edition rate. Cards that claim the same spend (same table, query or billing project) are compounded instead of added; Editions and byte-cap cards only count against what the table and query fixes leave; and the total can never exceed the last 30 days' actual compute spend.",
    },
    {
      title: "Review queue",
      icon: ListChecks,
      value: k.engineering_pending_count ?? k.pending_count,
      fmt: (n: number) => Math.round(n).toString(),
      foot: k.finops_pending_count
        ? `+ ${k.finops_pending_count} in FinOps & billing (${usd(k.finops_monthly_savings)}/mo)`
        : `${Object.values(k.class_counts || {}).filter(Boolean).length} optimization classes`,
      pill: "open",
      tone: "sky",
      onClick: () => onTab("queue"),
    },
    {
      title: "Safety guardrails",
      icon: ShieldAlert,
      value: k.blocked_count,
      fmt: (n: number) => Math.round(n).toString(),
      foot: k.blocked_count ? "Halted items need an operator" : "100% safe execution rate",
      pill: k.blocked_count ? "action" : "clear",
      tone: k.blocked_count ? "rose" : "emerald",
      onClick: () => onTab("blocked"),
    },
    {
      title: "Incident audit",
      icon: History,
      value: k.regressed_count || k.rolled_back_count,
      fmt: (n: number) => Math.round(n).toString(),
      foot: k.regressed_count
        ? `${k.regressed_count} active regression${k.regressed_count > 1 ? "s" : ""}`
        : k.rolled_back_count
          ? `${k.rolled_back_count} rolled back`
          : "Zero regressions logged",
      pill: k.regressed_count ? "alert" : "stable",
      tone: k.regressed_count ? "rose" : k.rolled_back_count ? "amber" : "emerald",
      onClick: () => onTab(k.regressed_count ? "regressed" : "rolled_back"),
    },
  ] as { title: string; icon: typeof Wallet; value: number; fmt: (n: number) => string; foot: string;
         pill: string; tone: string; onClick: undefined | (() => void); tip?: string }[];
  const tones: Record<string, string> = {
    emerald: "bg-emerald-500/10 text-emerald-400 ring-emerald-500/20",
    sky: "bg-sky-500/10 text-sky-300 ring-sky-500/20",
    rose: "bg-rose-500/10 text-rose-400 ring-rose-500/20",
    amber: "bg-amber-500/10 text-amber-300 ring-amber-500/20",
  };

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {items.map((it, i) => (
        <motion.button
          key={it.title}
          type="button"
          onClick={it.onClick}
          disabled={!it.onClick}
          title={it.tip}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.06 }}
          className={cn(
            "panel group relative overflow-hidden p-5 text-left transition-all duration-300",
            it.onClick && "hover:-translate-y-0.5 hover:border-sky-500/40 hover:shadow-glow",
            !it.onClick && "cursor-default",
          )}
        >
          <div className="pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full bg-sky-500/5 blur-2xl transition-opacity group-hover:opacity-100" />
          <div className="flex items-center justify-between">
            <span className="label">{it.title}</span>
            <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1", tones[it.tone])}>
              <ArrowUpRight className="h-3 w-3" />
              {it.pill}
            </span>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <it.icon className="h-5 w-5 text-zinc-400" />
            <div className="font-mono text-3xl font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
              <AnimatedNumber value={it.value || 0} format={it.fmt} />
            </div>
          </div>
          <div className="mt-2 text-xs text-zinc-500">{it.foot}</div>
        </motion.button>
      ))}
    </div>
  );
}
