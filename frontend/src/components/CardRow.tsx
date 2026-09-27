import { motion } from "framer-motion";
import { Users } from "lucide-react";
import type { ChangeSet } from "@/lib/api";
import { Badge, Meter } from "./ui";
import { cn, CLASS_META, needsTwo, targetLabel, usd } from "@/lib/utils";

export function CardRow({
  c,
  index,
  selected,
  compact,
  onClick,
}: {
  c: ChangeSet;
  index: number;
  selected: boolean;
  compact: boolean;
  onClick: () => void;
}) {
  const meta = CLASS_META[c.apply_class] || CLASS_META[1];
  const partial = (c.approvals || []).length > 0;
  return (
    <motion.button
      layout
      type="button"
      onClick={onClick}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.025, 0.4), layout: { duration: 0.25 } }}
      className={cn(
        "panel group relative w-full overflow-hidden p-4 text-left transition-all duration-200",
        "hover:-translate-y-0.5 hover:border-sky-500/40 hover:shadow-glow",
        selected && "border-sky-500/60 bg-sky-50 shadow-glow dark:border-sky-500/50 dark:bg-sky-500/[0.06]",
      )}
    >
      <span className={cn("absolute inset-y-0 left-0 w-1", meta.dot, selected ? "opacity-100" : "opacity-50 group-hover:opacity-100")} />
      <div className="flex items-start justify-between gap-3 pl-1.5">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <Badge className={cn(meta.color, meta.ring)}>{meta.short}</Badge>
            {(c.rule_ids || []).slice(0, 2).map((r) => (
              <Badge key={r} className="font-mono">{r}</Badge>
            ))}
            {needsTwo(c) && (
              <Badge className={partial ? "bg-amber-500/10 text-amber-300 ring-amber-500/30" : ""} title="Two-person approval">
                <Users className="h-3 w-3" /> {partial ? "1/2 signed" : "2-person"}
              </Badge>
            )}
          </div>
          <div className="truncate font-mono text-[13px] font-medium text-zinc-800 dark:text-zinc-100" title={targetLabel(c)}>
            {targetLabel(c)}
          </div>
          {!compact && (
            <div className="mt-1 line-clamp-2 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">{c.finding_summary}</div>
          )}
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-lg font-semibold text-emerald-500 dark:text-emerald-400">{usd(c.net_monthly_value_usd)}</div>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500">net / mo</div>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-3 pl-1.5">
        <span className="label !text-[10px]">Confidence</span>
        <Meter value={(c.confidence || 0) * 100} className="flex-1" />
        <span className="w-9 text-right font-mono text-[11px] text-zinc-500">{Math.round((c.confidence || 0) * 100)}%</span>
      </div>
    </motion.button>
  );
}
