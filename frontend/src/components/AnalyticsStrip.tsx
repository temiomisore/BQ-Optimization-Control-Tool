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

export function AnalyticsStrip({ data, onClass }: { data: Dashboard; onClass: (cls: number) => void }) {
  const dark = useDark();
  const k = data.kpis;
  const classData = [1, 2, 3, 4].map((c, i) => ({
    name: `Class ${c}`,
    value: Math.round(k.class_savings?.[c] || 0),
    count: k.class_counts?.[c] || 0,
    itemStyle: { color: CLASS_COLORS[i] },
  }));

  const donut = {
    ...baseTheme(dark),
    tooltip: {
      ...baseTheme(dark).tooltip,
      trigger: "item",
      formatter: (p: any) => `${p.name}<br/><b>${usd(p.value)}/mo</b> · ${p.data.count} cards (${p.percent}%)`,
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
          formatter: () => `{v|${usd(k.monthly_savings)}}\n{l|per month}`,
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
  const bars = w && {
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
      data: ["On-Demand", `Opt 1 · ${w.baseline}+→${w.max}`, `Opt 2 · 0→${w.max}`],
    },
    series: [
      {
        type: "bar",
        barWidth: 14,
        itemStyle: { borderRadius: [0, 6, 6, 0] },
        label: { show: true, position: "right", formatter: (p: any) => usd(p.value), fontFamily: "JetBrains Mono", fontSize: 11, color: dark ? "#e4e4e7" : "#27272a" },
        data: [
          { value: Math.round(w.od), itemStyle: { color: "#f43f5e" } },
          { value: Math.round(w.o1), itemStyle: { color: "#38bdf8" } },
          { value: Math.round(w.o2), itemStyle: { color: "#10b981" } },
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
          onEvents={{ click: (p: any) => onClass(Number(String(p.name).replace("Class ", ""))) }}
        />
      </div>

      <div className={panel}>
        <div className="mb-2 flex items-center justify-between">
          <span className="label">Project-wide billing fit · W-01</span>
          {w && <span className="font-mono text-[11px] text-sky-400">{w.tib.toLocaleString()} TiB / 30d</span>}
        </div>
        {bars ? (
          <>
            <ReactECharts option={bars} style={{ height: 170 }} notMerge />
            <div className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
              <div className="rounded-lg bg-sky-500/10 px-2.5 py-1.5 text-sky-300">Option 1 saves {100 - w.o1_pct}%</div>
              <div className="rounded-lg bg-emerald-500/10 px-2.5 py-1.5 text-emerald-300">Option 2 saves {100 - w.o2_pct}%</div>
            </div>
          </>
        ) : (
          <div className="grid h-[200px] place-items-center text-sm text-zinc-500">No W-01 sizing yet — run the rules engine.</div>
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
