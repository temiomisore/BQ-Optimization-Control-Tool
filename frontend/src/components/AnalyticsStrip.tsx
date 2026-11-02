import { useEffect, useState } from "react";
import ReactECharts from "echarts-for-react";
import { motion } from "framer-motion";
import { Bot, UserRound, Workflow } from "lucide-react";
import type { Dashboard } from "@/lib/api";
import { usd } from "@/lib/utils";

const CLASS_COLORS = ["#38bdf8", "#f59e0b", "#f43f5e", "#8b5cf6"];

function useDark() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const el = document.documentElement;
    const obs = new MutationObserver(() => setDark(el.classList.contains("dark")));
    obs.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

function baseTheme(dark: boolean) {
  return {
    textStyle: { fontFamily: "DM Sans, sans-serif", color: dark ? "#a1a1aa" : "#52525b" },
    tooltip: {
      backgroundColor: dark ? "rgba(12,12,15,0.95)" : "rgba(255,255,255,0.95)",
      borderColor: dark ? "#27272a" : "#e2e8f0",
      textStyle: { color: dark ? "#fafafa" : "#0f172a", fontFamily: "DM Sans, sans-serif" },
    },
  };
}

export type CardTab = "queue" | "finops";

export function AnalyticsStrip({ data, onClass }: { data: Dashboard; onClass: (cls: number, tab: CardTab) => void }) {
  const dark = useDark();
  const k = data.kpis;
  // Per class, split by where the cards live: the Review queue (engineering) or the
  // FinOps & billing tab (W-01/W-02 etc.), so the tooltip ties out to the lists below.
  const split = [1, 2, 3, 4].map((c) => {
    const inClass = (data.cards || []).filter((x) => Number(x.apply_class || 1) === c);
    const sum = (xs: typeof inClass) => xs.reduce((s, x) => s + Number(x.net_monthly_value_usd || 0), 0);
    const finops = inClass.filter((x) => x.governance_scope === "FINOPS");
    const queue = inClass.filter((x) => x.governance_scope !== "FINOPS");
    return { queueN: queue.length, queueUsd: sum(queue), finopsN: finops.length, finopsUsd: sum(finops) };
  });
  const classData = [1, 2, 3, 4].map((c, i) => ({
    name: `Class ${c}`,
    value: Math.round(k.class_savings?.[c] || 0),
    count: k.class_counts?.[c] || 0,
    split: split[i],
    itemStyle: { color: CLASS_COLORS[i] },
  }));
  const cardsTotal = k.monthly_savings_gross_sum ?? classData.reduce((s, d) => s + d.value, 0);
  const plural = (n: number) => `${n} card${n === 1 ? "" : "s"}`;

  const donut = {
    ...baseTheme(dark),
    tooltip: {
      ...baseTheme(dark).tooltip,
      trigger: "item",
      formatter: (p: any) => {
        const s = p.data.split;
        const rows = [
          `Review queue: ${plural(s.queueN)} · <b>${usd(s.queueUsd)}/mo</b>`,
          ...(s.finopsN ? [`FinOps &amp; billing tab: ${plural(s.finopsN)} · <b>${usd(s.finopsUsd)}/mo</b>`] : []),
        ];
        return `${p.name} · cards total <b>${usd(p.value)}/mo</b> (${p.percent}%)<br/>${rows.join("<br/>")}`;
      },
    },
    legend: { bottom: 0, icon: "circle", itemWidth: 8, textStyle: { color: dark ? "#a1a1aa" : "#52525b", fontSize: 11 } },
    series: [
      {
        type: "pie",
        radius: ["58%", "80%"],
        center: ["50%", "44%"],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: dark ? "#0c0c0f" : "#fff", borderWidth: 3, borderRadius: 6 },
        label: {
          show: true,
          position: "center",
          formatter: () => `{v|${usd(k.monthly_savings)}}\n{l|net / mo}`,
          rich: {
            v: { fontFamily: "JetBrains Mono", fontSize: 18, fontWeight: 700, color: dark ? "#fafafa" : "#09090b" },
            l: { fontSize: 11, color: "#71717a", padding: [4, 0, 0, 0] },
          },
        },
        emphasis: { scale: true, scaleSize: 6 },
        data: classData,
      },
    ],
  };

  const w = data.w01;
  const w3 = data.w03;
  const capChart = w
    ? {
        curLabel: "On-Demand",
        curVal: w.od,
        opt1Label: `Opt 1 · ${w.baseline}+→${w.max}`,
        opt1Val: w.o1,
        opt2Label: `Opt 2 · 0→${w.max}`,
        opt2Val: w.o2,
        opt1Pct: Math.max(0, 100 - w.o1_pct),
        opt2Pct: Math.max(0, 100 - w.o2_pct),
      }
    : w3
      ? {
          curLabel: `${w3.reservation_name} (${w3.cur_baseline}+)`,
          curVal: w3.cur_cost,
          opt1Label: `Opt 1 · ${w3.rec_baseline}+→${w3.rec_max}`,
          opt1Val: w3.o1,
          opt2Label: w3.option_2_mode === "ON_DEMAND" ? "Opt 2 · On-Demand" : `Opt 2 · 0→${w3.rec_max}`,
          opt2Val: w3.o2,
          opt1Pct: w3.cur_cost > 0 ? Math.max(0, Math.round((100 * (w3.cur_cost - w3.o1)) / w3.cur_cost)) : 0,
          opt2Pct: w3.cur_cost > 0 ? Math.max(0, Math.round((100 * (w3.cur_cost - w3.o2)) / w3.cur_cost)) : 0,
        }
      : null;

  const bars = capChart && {
    ...baseTheme(dark),
    tooltip: { ...baseTheme(dark).tooltip, trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v: number) => `${usd(v)}/mo` },
    grid: { left: 8, right: 56, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: "value", show: false },
    yAxis: {
      type: "category",
      inverse: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: dark ? "#d4d4d8" : "#3f3f46", fontSize: 11 },
      data: [capChart.curLabel, capChart.opt1Label, capChart.opt2Label],
    },
    series: [
      {
        type: "bar",
        barWidth: 14,
        itemStyle: { borderRadius: [0, 6, 6, 0] },
        label: { show: true, position: "right", formatter: (p: any) => usd(p.value), fontFamily: "JetBrains Mono", fontSize: 11, color: dark ? "#e4e4e7" : "#27272a" },
        data: [
          { value: Math.round(capChart.curVal), itemStyle: { color: "#f43f5e" } },
          { value: Math.round(capChart.opt1Val), itemStyle: { color: "#38bdf8" } },
          { value: Math.round(capChart.opt2Val), itemStyle: { color: "#10b981" } },
        ],
      },
    ],
  };

  const panel = "panel p-5";
  return (
    <motion.section
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      className="grid grid-cols-1 gap-4 overflow-hidden lg:grid-cols-3"
    >
      <div className={panel}>
        <div className="mb-2 flex items-center justify-between">
          <span className="label">Savings by optimization class</span>
          <span className="text-[11px] text-zinc-500">click a slice to filter</span>
        </div>
        <ReactECharts
          option={donut}
          style={{ height: 220 }}
          notMerge
          onEvents={{
            click: (p: any) => {
              const s = p.data?.split;
              onClass(Number(String(p.name).replace("Class ", "")), s && !s.queueN && s.finopsN ? "finops" : "queue");
            },
          }}
        />
        <p className="mt-1 text-center text-[11px] leading-snug text-zinc-500">
          Slices = card totals ({usd(cardsTotal)}/mo). Cards that target the same spend are counted once in the{" "}
          {usd(k.monthly_savings)} net.
          {k.headline_capped_at_spend && (
            <>
              {" "}
              {/* The bill amount is only sent to FinOps viewers (server nulls it for everyone else). */}
              <span className="font-medium text-amber-500">
                Capped at your actual 30-day query bill
                {k.compute_spend_30d_usd != null ? ` (${usd(k.compute_spend_30d_usd)})` : ""}. You can't save more than you
                spend.
              </span>
            </>
          )}
        </p>
      </div>

      <div className={panel}>
        <div className="mb-2 flex items-center justify-between">
          <span className="label">{w ? "Slot & billing fit · W-01 / W-03" : "Reservation right-sizing · W-03"}</span>
          {w ? (
            <span className="font-mono text-[11px] text-sky-400">
              {w.tib.toLocaleString()} TiB/mo · {w.sizing_window_days || 30}d
            </span>
          ) : w3 ? (
            <span className="font-mono text-[11px] text-sky-400">
              {w3.edition} · {w3.sizing_window_days || 30}d
            </span>
          ) : null}
        </div>
        {bars && capChart ? (
          <>
            <ReactECharts option={bars} style={{ height: w && w3 ? 150 : 170 }} notMerge />
            <div className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
              <div className="rounded-lg bg-sky-500/10 px-2.5 py-1.5 text-sky-300">Option 1 · {capChart.opt1Pct}% lower</div>
              <div className="rounded-lg bg-emerald-500/10 px-2.5 py-1.5 text-emerald-300">Option 2 · {capChart.opt2Pct}% lower</div>
            </div>
            {w && w3 && (
              <div className="mt-1.5 truncate rounded-md border border-zinc-200 px-2 py-1 font-mono text-[10px] text-zinc-500 dark:border-ink-800">
                W-03 · {w3.reservation_name}: {w3.cur_baseline}→{w3.rec_baseline} baseline ({w3.rec_max} max)
              </div>
            )}
          </>
        ) : (
          <div className="grid h-[200px] place-items-center text-sm text-zinc-500">No W-01 / W-03 capacity sizing yet — run the rules engine.</div>
        )}
      </div>

      <div className={panel}>
        <div className="mb-3 flex items-center justify-between">
          <span className="label">Guardrail isolation · W-02</span>
          <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-400">0% ETL risk</span>
        </div>
        <div className="flex flex-col gap-2.5 text-sm">
          {[
            { icon: UserRound, who: "Human ad-hoc analysts", what: "50 GiB cap + 50-slot pool", tone: "text-emerald-300 bg-emerald-500/10" },
            { icon: Bot, who: "Service accounts", what: "100% exempt · uncapped", tone: "text-sky-300 bg-sky-500/10" },
            { icon: Workflow, who: "Airflow / dbt / Dataform", what: "100% exempt · prod pool", tone: "text-sky-300 bg-sky-500/10" },
          ].map((r) => (
            <div key={r.who} className="flex items-center justify-between gap-3 rounded-lg border border-zinc-200 px-3 py-2.5 dark:border-ink-800">
              <span className="flex items-center gap-2 text-zinc-700 dark:text-zinc-200">
                <r.icon className="h-4 w-4 text-zinc-400" /> {r.who}
              </span>
              <span className={`rounded-md px-2 py-0.5 font-mono text-[11px] ${r.tone}`}>{r.what}</span>
            </div>
          ))}
        </div>
      </div>
    </motion.section>
  );
}
