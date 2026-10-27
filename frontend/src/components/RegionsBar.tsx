import { motion } from "framer-motion";
import { BellRing, Globe2, Home } from "lucide-react";
import type { RegionInfo, RegionsInfo } from "@/lib/api";
import { ago, cn, fmtDate } from "@/lib/utils";

/** Which BigQuery locations the collector visits, plus a banner for newly detected regions.
 *  Data: optimizer_ops.regions_seen (written by optimizer/regions.py on every collector run). */
export function RegionsBar({ info }: { info?: RegionsInfo }) {
  if (!info || !info.regions?.length) return null;
  const auto = info.mode === "auto";
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const chipTitle = (r: RegionInfo) =>
    r.dataset_count != null
      ? `${plural(r.dataset_count, "dataset")} in ${plural(r.project_count || 0, "project")}` +
        (r.first_seen_at ? ` · first seen ${fmtDate(r.first_seen_at)}` : "")
      : "From config.yaml (not discovered yet)";

  return (
    <div className="space-y-2">
      {info.alerts.map((r) => (
        <motion.div
          key={`alert-${r.region}`}
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex flex-wrap items-center gap-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4"
        >
          <BellRing className="h-5 w-5 shrink-0 text-amber-400" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-amber-200">
              New region detected · <span className="font-mono">{r.region}</span>
            </div>
            <div className="text-xs text-amber-200/80">
              {plural(r.dataset_count || 0, "dataset")} in {plural(r.project_count || 0, "project")}, first seen{" "}
              {fmtDate(r.first_seen_at, true)}. The collector now includes it every night. Check whether it was created
              on purpose, since queries there won't use a reservation in another region.
            </div>
          </div>
        </motion.div>
      ))}

      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-zinc-200 bg-white/60 px-4 py-2.5 text-xs dark:border-ink-800 dark:bg-white/[0.02]">
        <Globe2 className="h-4 w-4 text-sky-400" />
        <span className="font-medium text-zinc-700 dark:text-zinc-200">
          Watching {plural(info.regions.length, "region")}
        </span>
        <span className="text-zinc-500">
          {auto
            ? info.discovered
              ? `· auto-discovered (Cloud Asset Inventory)${info.last_run ? `, last run ${ago(info.last_run)}` : ""}`
              : "· auto-discovery on, waiting for the first collector run"
            : "· fixed list in config.yaml"}
        </span>
        <div className="flex flex-wrap gap-1.5">
          {info.regions.map((r) => (
            <span
              key={r.region}
              title={chipTitle(r)}
              className={cn(
                "inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-mono text-[11px]",
                r.alert
                  ? "bg-amber-500/15 text-amber-300"
                  : r.home
                    ? "bg-emerald-500/15 text-emerald-300"
                    : "bg-sky-500/10 text-sky-300",
              )}
            >
              {r.home && <Home className="h-3 w-3" />}
              {r.region}
              {r.dataset_count != null && <span className="font-sans text-[10px] opacity-70">· {r.dataset_count}</span>}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
