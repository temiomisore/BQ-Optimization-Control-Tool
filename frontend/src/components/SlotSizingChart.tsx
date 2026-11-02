import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { CapacitySizing } from "@/lib/api";

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

const fmtSlots = (n: number) => {
  if (!Number.isFinite(n) || n <= 0) return "0";
  if (n >= 100 || Number.isInteger(n)) return Math.round(n).toLocaleString();
  return n < 10 ? n.toFixed(1).replace(/\.0$/, "") : Math.round(n).toLocaleString();
};

export interface SlotSizingChartProps {
  sizing?: CapacitySizing | null;
  recBaseline: number;
  recMax: number;
  curBaseline?: number | null;
  curMax?: number | null;
  isReservation?: boolean;
  selectedOption?: number;
  option2Mode?: string;
  bytesTib30d?: number | null;
  windowDays?: number;
  compact?: boolean;
}

interface BarCol {
  key: string;
  label: string;
  baseVal: number;
  burstVal: number;
  baseColor: string;
  burstColor?: string;
  burstBorder?: string;
  tooltipTitle: string;
  tooltipBody: string;
}

/**
 * Visualizes WHY the rules engine chose `recBaseline` (from all-minute median p50 rounded down
 * to 50) and `recMax` autoscale ceiling (from busy-minute p99 rounded up to 50).
 */
export function SlotSizingChart({
  sizing,
  recBaseline,
  recMax,
  curBaseline,
  curMax,
  isReservation = false,
  selectedOption = 1,
  option2Mode = "PURE_AUTOSCALE",
  bytesTib30d,
  windowDays,
  compact = false,
}: SlotSizingChartProps) {
  const dark = useDark();
  const safeBase = Math.max(0, Math.round(Number(recBaseline || 0)));
  const safeMax = Math.max(safeBase, Math.round(Number(recMax || 0)));
  const recAutoAdd = Math.max(0, safeMax - safeBase);

  const cBase = Math.max(0, Math.round(Number(curBaseline ?? sizing?.current_baseline_slots ?? 0)));
  const cMaxRaw = Math.max(cBase, Math.round(Number(curMax ?? sizing?.current_max_slots ?? 0)));
  const cAutoAdd = Math.max(0, cMaxRaw - cBase);

  const hasTelemetry =
    sizing != null &&
    sizing.source !== "DEFAULT_NO_TELEMETRY" &&
    (sizing.p50_slots_all_minutes != null || sizing.p99_slots_busy_minutes != null || sizing.peak_slots_1min != null);

  const p50 = Number(sizing?.p50_slots_all_minutes ?? 0);
  const p95 = Number(sizing?.p95_slots_all_minutes ?? 0);
  const p50Busy = sizing?.p50_slots_busy_minutes != null ? Number(sizing.p50_slots_busy_minutes) : null;
  const p99Busy = Number(sizing?.p99_slots_busy_minutes ?? 0);
  const peak = Number(sizing?.peak_slots_1min ?? 0);
  const busyPct = sizing?.busy_minutes_pct != null ? Number(sizing.busy_minutes_pct) : null;
  const idlePct = busyPct != null ? Math.max(0, Math.round((100 - busyPct) * 10) / 10) : null;
  const days = Number(sizing?.sizing_window_days || windowDays || 30);

  const isOpt2 = selectedOption === 2;
  const isReturnToOd = isOpt2 && option2Mode === "ON_DEMAND";

  const cols = useMemo<BarCol[]>(() => {
    const out: BarCol[] = [];

    if (isReservation && cMaxRaw > 0) {
      out.push({
        key: "today",
        label: compact ? "Today" : "Today's\npool",
        baseVal: cBase,
        burstVal: cAutoAdd,
        baseColor: "#fb7185",
        burstColor: dark ? "rgba(244,63,94,0.34)" : "rgba(244,63,94,0.26)",
        burstBorder: "#f43f5e",
        tooltipTitle: "Currently provisioned reservation",
        tooltipBody: `${cBase.toLocaleString()} baseline + ${cAutoAdd.toLocaleString()} autoscale = <b>${cMaxRaw.toLocaleString()} max slots</b>.`,
      });
    }

    if (hasTelemetry) {
      out.push({
        key: "p50",
        label: compact ? "p50" : "Median\n(p50 all)",
        baseVal: p50,
        burstVal: 0,
        baseColor: "#38bdf8",
        tooltipTitle: "Why Baseline = " + safeBase.toLocaleString() + " slots (p50 all minutes)",
        tooltipBody:
          `50% of all clock minutes over ${days}d (including nights &amp; weekends) used <b>&le; ${fmtSlots(p50)} slots</b>` +
          (idlePct != null ? ` (${idlePct}% of minutes were completely idle)` : "") +
          `.<br/>Rounding down to a 50-slot step gives <b>${safeBase.toLocaleString()} baseline slots</b> so you never pay 24/7 for idle capacity.`,
      });

      out.push({
        key: "p95",
        label: compact ? "p95" : "High\n(p95 all)",
        baseVal: p95,
        burstVal: 0,
        baseColor: "#22d3ee",
        tooltipTitle: "95th percentile (all clock minutes)",
        tooltipBody: `95% of all minutes over ${days}d used <b>&le; ${fmtSlots(p95)} slots</b>.`,
      });

      if (!compact && p50Busy != null && p50Busy > p50 + 5) {
        out.push({
          key: "p50_busy",
          label: "Active\n(p50 busy)",
          baseVal: p50Busy,
          burstVal: 0,
          baseColor: "#34d399",
          tooltipTitle: "Typical active query minute (p50 busy)",
          tooltipBody: `When queries are actively running, median slot demand is <b>${fmtSlots(p50Busy)} slots</b>.`,
        });
      }

      out.push({
        key: "p99_busy",
        label: compact ? "p99 busy" : "Busy rush\n(p99)",
        baseVal: p99Busy,
        burstVal: 0,
        baseColor: "#10b981",
        tooltipTitle: `Why Max Ceiling = ${safeMax.toLocaleString()} slots (p99 busy minutes)`,
        tooltipBody:
          `99% of active query minutes fit inside <b>&le; ${fmtSlots(p99Busy)} slots</b>.<br/>` +
          `Rounding up to BigQuery's 50-slot step sets a <b>${safeMax.toLocaleString()}-slot ceiling</b> ` +
          `(${safeBase.toLocaleString()} baseline + ${recAutoAdd.toLocaleString()} autoscale) to cover 99% of bursts.`,
      });

      out.push({
        key: "peak",
        label: compact ? "Peak" : "1-min\npeak",
        baseVal: peak,
        burstVal: 0,
        baseColor: "#f59e0b",
        tooltipTitle: "Highest single-minute spike",
        tooltipBody:
          `Peak 1-minute slot demand over ${days}d was <b>${fmtSlots(peak)} slots</b>.<br/>` +
          `Brief queuing on rare 1-minute outliers beats paying 24/7 for peak capacity.`,
      });
    }

    if (isReturnToOd) {
      out.push({
        key: "opt1_ref",
        label: compact ? "Opt 1" : "Opt 1\n(slots)",
        baseVal: safeBase,
        burstVal: recAutoAdd,
        baseColor: dark ? "rgba(14,165,233,0.45)" : "rgba(14,165,233,0.4)",
        burstColor: dark ? "rgba(16,185,129,0.22)" : "rgba(16,185,129,0.2)",
        burstBorder: "#10b981",
        tooltipTitle: `Option 1 slot sizing (${safeBase} + ${recAutoAdd})`,
        tooltipBody:
          `If kept on slots: <b>${safeBase.toLocaleString()} baseline</b> + <b>${recAutoAdd.toLocaleString()} autoscale</b> ` +
          `(50 slots is BigQuery's smallest autoscale step).`,
      });
      out.push({
        key: "opt2_od",
        label: compact ? "Opt 2 ★" : "Opt 2 ★\nOn-Demand",
        baseVal: 0,
        burstVal: 0,
        baseColor: "#10b981",
        tooltipTitle: "Option 2 selected: Return to On-Demand ($6.25/TiB)",
        tooltipBody:
          `Drops reserved slots to <b>0</b> and bills per TiB scanned` +
          (bytesTib30d != null ? ` (${bytesTib30d.toLocaleString()} TiB/mo)` : "") +
          ` — more cost-efficient than even a 50-slot pool.`,
      });
    } else if (isOpt2) {
      out.push({
        key: "sized_opt2",
        label: compact ? "Opt 2" : "Opt 2\n(0 + auto)",
        baseVal: 0,
        burstVal: safeMax,
        baseColor: "#0ea5e9",
        burstColor: dark ? "rgba(16,185,129,0.52)" : "rgba(16,185,129,0.42)",
        burstBorder: "#10b981",
        tooltipTitle: `Option 2: 0 Baseline + ${safeMax.toLocaleString()} Pure Autoscale`,
        tooltipBody:
          `<b>0 baseline slots</b> ($0/hr when idle) + elastic autoscaling up to <b>${safeMax.toLocaleString()} slots</b> ` +
          `in 50-slot / 1-min increments.`,
      });
    } else {
      out.push({
        key: "sized_opt1",
        label: compact ? "Sized" : "Sized\npool",
        baseVal: safeBase,
        burstVal: recAutoAdd,
        baseColor: "#0ea5e9",
        burstColor: dark ? "rgba(16,185,129,0.52)" : "rgba(16,185,129,0.42)",
        burstBorder: "#10b981",
        tooltipTitle: `Recommended capacity (${safeBase.toLocaleString()} baseline + ${recAutoAdd.toLocaleString()} autoscale)`,
        tooltipBody:
          `<b>${safeBase.toLocaleString()} baseline slots</b> (24/7 steady floor from p50) + ` +
          `<b>${recAutoAdd.toLocaleString()} autoscale slots</b> (burst ceiling ${safeMax.toLocaleString()} from p99 busy).`,
      });
    }

    return out;
  }, [
    isReservation, cMaxRaw, cBase, cAutoAdd, dark, compact, hasTelemetry,
    p50, p95, p50Busy, p99Busy, peak, safeBase, safeMax, recAutoAdd, days, idlePct,
    isReturnToOd, isOpt2, bytesTib30d,
  ]);

  const yMax = useMemo(() => {
    const rawMax = Math.max(
      safeMax,
      isReservation ? cMaxRaw : 0,
      hasTelemetry ? Math.max(p50, p95, p99Busy, peak, p50Busy ?? 0) : 0,
      50,
    );
    return Math.ceil((rawMax * 1.18) / 50) * 50;
  }, [safeMax, isReservation, cMaxRaw, hasTelemetry, p50, p95, p99Busy, peak, p50Busy]);

  const linesClose = safeBase > 0 && safeMax > 0 && (safeMax - safeBase) / Math.max(yMax, 1) < 0.18;

  const option = useMemo(() => {
    const labelColor = dark ? "#e4e4e7" : "#27272a";
    const axisText = dark ? "#a1a1aa" : "#52525b";
    const splitColor = dark ? "rgba(255,255,255,0.06)" : "rgba(15,23,42,0.06)";

    const markLines: any[] = [];
    if (safeBase > 0 && !isOpt2) {
      markLines.push({
        yAxis: safeBase,
        lineStyle: { color: "#38bdf8", type: "dashed", width: 1.5 },
        label: {
          show: true,
          position: "end",
          distance: 6,
          offset: linesClose ? [0, 5] : [0, 0],
          formatter: compact ? `Base ${safeBase}` : `Baseline ${safeBase}`,
          color: dark ? "#7dd3fc" : "#0284c7",
          fontFamily: "JetBrains Mono, monospace",
          fontSize: 10,
          fontWeight: 600,
        },
      });
    }
    if (safeMax > 0 && !isReturnToOd) {
      markLines.push({
        yAxis: safeMax,
        lineStyle: { color: "#10b981", type: "dashed", width: 1.5 },
        label: {
          show: true,
          position: "end",
          distance: 6,
          offset: linesClose ? [0, -5] : [0, 0],
          formatter: compact ? `Max ${safeMax}` : `Max ${safeMax}`,
          color: dark ? "#6ee7b7" : "#059669",
          fontFamily: "JetBrains Mono, monospace",
          fontSize: 10,
          fontWeight: 600,
        },
      });
    }

    const baseData = cols.map((col) => {
      const hasTop = col.burstVal > 0;
      const tot = col.baseVal + col.burstVal;
      return {
        value: Number(col.baseVal.toFixed(1)),
        col,
        itemStyle: {
          color: col.baseColor,
          borderRadius: hasTop ? [0, 0, 0, 0] : [5, 5, 0, 0],
        },
        label: {
          show: !hasTop,
          position: "top",
          distance: 3,
          formatter: () => (col.key === "opt2_od" ? "0 (OD)" : fmtSlots(tot)),
          fontFamily: "JetBrains Mono, monospace",
          fontSize: compact ? 10 : 10.5,
          color: col.key === "opt2_od" ? (dark ? "#6ee7b7" : "#059669") : labelColor,
        },
      };
    });

    const burstData = cols.map((col) => {
      const hasTop = col.burstVal > 0;
      const tot = col.baseVal + col.burstVal;
      return {
        value: hasTop ? Number(col.burstVal.toFixed(1)) : 0,
        col,
        itemStyle: {
          color: hasTop ? col.burstColor || "rgba(16,185,129,0.45)" : "transparent",
          borderColor: hasTop ? col.burstBorder || "#10b981" : "transparent",
          borderWidth: hasTop ? 1.2 : 0,
          borderRadius: hasTop ? [5, 5, 0, 0] : [0, 0, 0, 0],
        },
        label: {
          show: hasTop,
          position: "top",
          distance: 3,
          formatter: () =>
            !compact && col.baseVal > 0 && col.burstVal > 0
              ? `${fmtSlots(tot)} (${fmtSlots(col.baseVal)}+${fmtSlots(col.burstVal)})`
              : fmtSlots(tot),
          fontFamily: "JetBrains Mono, monospace",
          fontSize: compact ? 10 : 10.5,
          color: labelColor,
        },
      };
    });

    return {
      textStyle: { fontFamily: "DM Sans, sans-serif", color: axisText },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        confine: true,
        backgroundColor: dark ? "rgba(12,12,15,0.96)" : "rgba(255,255,255,0.97)",
        borderColor: dark ? "#27272a" : "#e2e8f0",
        textStyle: { color: dark ? "#fafafa" : "#0f172a", fontFamily: "DM Sans, sans-serif", fontSize: 12 },
        formatter: (params: any) => {
          const first = Array.isArray(params) ? params[0] : params;
          const col: BarCol | undefined = first?.data?.col;
          if (!col) return "";
          return `<div style="max-width:270px;white-space:normal;line-height:1.45"><div style="font-weight:600;margin-bottom:3px">${col.tooltipTitle}</div><div style="color:${dark ? "#d4d4d8" : "#3f3f46"};font-size:11px">${col.tooltipBody}</div></div>`;
        },
      },
      grid: {
        left: 6,
        right: compact ? 58 : 78,
        top: 22,
        bottom: 4,
        containLabel: true,
      },
      xAxis: {
        type: "category",
        data: cols.map((c) => c.label),
        axisTick: { show: false },
        axisLine: { lineStyle: { color: splitColor } },
        axisLabel: {
          color: axisText,
          fontSize: compact ? 10 : 10.5,
          lineHeight: 13,
          interval: 0,
        },
      },
      yAxis: {
        type: "value",
        max: yMax,
        splitNumber: 3,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: splitColor } },
        axisLabel: {
          color: axisText,
          fontSize: 10,
          fontFamily: "JetBrains Mono, monospace",
        },
      },
      series: [
        {
          name: "Baseline / Observed",
          type: "bar",
          stack: "slots",
          barMaxWidth: compact ? 22 : 30,
          data: baseData,
          markLine: markLines.length
            ? {
                symbol: ["none", "none"],
                silent: true,
                animation: false,
                data: markLines,
              }
            : undefined,
        },
        {
          name: "Autoscale burst",
          type: "bar",
          stack: "slots",
          barMaxWidth: compact ? 22 : 30,
          data: burstData,
        },
      ],
    };
  }, [cols, dark, compact, yMax, safeBase, safeMax, isOpt2, isReturnToOd, linesClose]);

  return (
    <div className={compact ? "space-y-1.5" : "space-y-3 rounded-lg border border-zinc-200 p-3.5 dark:border-ink-800"}>
      {!compact && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-zinc-500">
          <div className="flex flex-wrap items-center gap-3">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm bg-sky-400" />
              Baseline / steady (p50)
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm border border-emerald-500 bg-emerald-500/40" />
              Autoscale headroom (p99)
            </span>
            {isReservation && cMaxRaw > 0 && (
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-sm border border-rose-500 bg-rose-500/40" />
                Today&apos;s cap
              </span>
            )}
          </div>
          <span className="font-mono text-[10px] text-zinc-500">
            {days}d scan{busyPct != null ? ` · ${busyPct}% active / ${idlePct}% idle mins` : ""}
          </span>
        </div>
      )}

      <ReactECharts option={option} style={{ height: compact ? 155 : 195 }} notMerge />

      {!hasTelemetry && !compact && (
        <div className="rounded-md bg-amber-500/10 px-2.5 py-1.5 text-[11px] text-amber-400">
          Minute-level <code>JOBS_TIMELINE</code> percentiles were not recorded on this card yet — run a fresh scan in{" "}
          <b>Settings</b> to plot <code>p50</code>, <code>p95</code>, <code>p99</code>, and peak bars.
        </div>
      )}

      {!compact && (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <div className="rounded-md border border-sky-500/25 bg-sky-500/[0.04] p-2.5 text-xs">
            <div className="flex items-center justify-between gap-2 font-medium text-sky-600 dark:text-sky-300">
              <span>Why {isOpt2 ? 0 : safeBase} baseline slots</span>
              <span className="rounded bg-sky-500/15 px-1.5 py-0.5 font-mono text-[10px]">
                {isOpt2 ? "Opt 2 = 0" : `p50 = ${fmtSlots(p50)} → ${safeBase}`}
              </span>
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">
              {isOpt2 ? (
                <>
                  Option 2 sets <b>0 baseline slots</b> so you pay <b>$0/hr</b> during the{" "}
                  {idlePct != null ? `${idlePct}% of minutes that sit idle` : "idle hours & weekends"}.
                  {safeBase > 0 ? ` (Option 1 sizes ${safeBase} baseline slots from p50 = ${fmtSlots(p50)}.)` : ""}
                </>
              ) : safeBase === 0 ? (
                <>
                  Median minute (<code>p50</code>) is <b>{fmtSlots(p50)} slots</b>
                  {idlePct != null ? ` (${idlePct}% of minutes are idle)` : ""}. Rounding down to 50 gives{" "}
                  <b>0 baseline slots</b> — never pay 24/7 for empty capacity.
                </>
              ) : (
                <>
                  Median minute (<code>p50</code>) across all {days}d clock minutes is <b>{fmtSlots(p50)} slots</b>.
                  Rounding down to BigQuery&apos;s 50-slot step gives <b>{safeBase} steady slots</b> that stay busy &ge;50% of the day.
                </>
              )}
            </p>
          </div>

          <div className="rounded-md border border-emerald-500/25 bg-emerald-500/[0.04] p-2.5 text-xs">
            <div className="flex items-center justify-between gap-2 font-medium text-emerald-600 dark:text-emerald-300">
              <span>
                {isReturnToOd
                  ? "Why 0 autoscale (On-Demand)"
                  : `Why +${isOpt2 ? safeMax : recAutoAdd} autoscale (${safeMax} max)`}
              </span>
              <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 font-mono text-[10px]">
                {isReturnToOd ? "Pay per TiB" : `p99 = ${fmtSlots(p99Busy)} → ${safeMax}`}
              </span>
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">
              {isReturnToOd ? (
                <>
                  Active bursts only need <b>&le; {fmtSlots(p99Busy)} slots</b> (<code>p99</code>), so even BigQuery&apos;s
                  smallest <b>50-slot</b> autoscale step costs more than paying <b>$6.25/TiB On-Demand</b>
                  {bytesTib30d != null ? ` for ${bytesTib30d.toLocaleString()} TiB/mo` : ""}.
                </>
              ) : (
                <>
                  99% of active query minutes (<code>p99 busy</code>) fit inside <b>&le; {fmtSlots(p99Busy)} slots</b>
                  {peak > 0 ? ` (1-min peak: ${fmtSlots(peak)})` : ""}. Rounding up to the next 50-slot step sets a{" "}
                  <b>{safeMax}-slot ceiling</b> that absorbs bursts in 60s steps.
                </>
              )}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
