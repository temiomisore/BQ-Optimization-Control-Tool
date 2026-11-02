import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { CapacitySizing, CapacityTimelinePoint } from "@/lib/api";
import { cn } from "@/lib/utils";

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

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const fmtSlots = (n: number) => {
  if (!Number.isFinite(n) || n <= 0) return "0";
  if (n >= 100 || Number.isInteger(n)) return Math.round(n).toLocaleString();
  return n < 20 ? n.toFixed(1).replace(/\.0$/, "") : Math.round(n).toLocaleString();
};

function fmtBucketAxis(raw: string): string {
  // Expected: "YYYY-MM-DD HH:MM" or "Day N"
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(raw);
  if (!m) return raw;
  const mon = MONTHS[Math.max(0, Math.min(11, Number(m[2]) - 1))];
  const day = Number(m[3]);
  return `${mon} ${day}`;
}

function fmtBucketTooltip(raw: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}:\d{2}))?/.exec(raw);
  if (!m) return raw;
  const mon = MONTHS[Math.max(0, Math.min(11, Number(m[2]) - 1))];
  const day = Number(m[3]);
  return m[4] ? `${mon} ${day}, ${m[4]} UTC` : `${mon} ${day}`;
}

/**
 * Deterministic fallback curve when `sizing.timeline` hasn't been hydrated yet (e.g. offline seed),
 * shaped strictly from the card's observed `(p50, p95, p99_busy, peak, busy_minutes_pct)` percentiles.
 */
function buildFallbackTimeline(
  days: number,
  p50: number,
  p95: number,
  p50Busy: number | null,
  p99Busy: number,
  peak: number,
  busyPct: number | null,
): CapacityTimelinePoint[] {
  const n = 48;
  const activeRatio = busyPct != null ? Math.max(0.06, Math.min(1, busyPct / 100)) : p50 > 0 ? 0.72 : 0.35;
  const activeMid = p50Busy != null && p50Busy > 0 ? p50Busy : Math.max(p50, p95 * 0.6, p99Busy * 0.35);
  const hiBurst = Math.max(p99Busy, p95, activeMid);
  const topPeak = Math.max(peak, hiBurst);
  const nowMs = Date.now();
  const spanMs = Math.max(7, days) * 86400 * 1000;

  const pts: CapacityTimelinePoint[] = [];
  for (let i = 0; i < n; i++) {
    const dt = new Date(nowMs - spanMs + (i / Math.max(1, n - 1)) * spanMs);
    const yyyy = dt.getUTCFullYear();
    const mm = String(dt.getUTCMonth() + 1).padStart(2, "0");
    const dd = String(dt.getUTCDate()).padStart(2, "0");
    const hh = String(dt.getUTCHours()).padStart(2, "0");
    const frac = i / (n - 1);

    // Natural idle -> ramp -> sustained burst -> peak -> cool-down wave (matching GCP capacity patterns)
    let demand = p50;
    if (frac < 1 - activeRatio && p50 <= 0) {
      demand = 0;
    } else {
      const wave = Math.sin(((frac - (1 - activeRatio)) / Math.max(activeRatio, 0.1)) * Math.PI);
      const ripple = 0.85 + 0.15 * Math.sin(i * 1.35);
      if (i === Math.round(n * 0.72)) {
        demand = topPeak;
      } else if (wave > 0.72) {
        demand = Math.min(topPeak, hiBurst * (0.88 + 0.12 * ripple));
      } else if (wave > 0.3) {
        demand = Math.max(p50, activeMid * (0.8 + 0.45 * wave * ripple));
      } else {
        demand = p50 > 0 ? p50 * (0.85 + 0.2 * ripple) : 0;
      }
    }
    pts.push({
      t: `${yyyy}-${mm}-${dd} ${hh}:00`,
      demand: Math.max(0, Math.round(demand * 10) / 10),
    });
  }
  return pts;
}

export interface SlotSizingChartProps {
  sizing?: CapacitySizing | null;
  recBaseline: number;
  recMax: number;
  curBaseline?: number | null;
  curMax?: number | null;
  committedSlots?: number | null;
  isReservation?: boolean;
  selectedOption?: number;
  option2Mode?: string;
  bytesTib30d?: number | null;
  windowDays?: number;
  compact?: boolean;
}

/**
 * Time-series slot capacity & demand chart (modeled on GCP BigQuery Autoscaling architecture):
 *   - Y-axis: Number of Slots
 *   - X-axis: Chronological usage across the scan window
 *   - Smooth amber `Demand` curve + light-blue `Baseline Slots` band + hatched `Committed Slots`
 *     band + stepped medium-blue `Autoscaling Slots` plateaus
 *   - Explicit horizontal lines for `Baseline` and `Autoscale limit` (+ `Today's cap` when oversized)
 */
export function SlotSizingChart({
  sizing,
  recBaseline,
  recMax,
  curBaseline,
  curMax,
  committedSlots,
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
  const commSlots = Math.max(
    0,
    Math.round(Number(committedSlots ?? sizing?.covered_baseline_slots ?? sizing?.committed_slots ?? 0)),
  );

  const isOpt2 = selectedOption === 2;
  const isReturnToOd = isOpt2 && option2Mode === "ON_DEMAND";

  // Even when Option 2 = Return to On-Demand is pre-selected as cheaper, we still plot the
  // consumption-based slot lines (safeBase + safeMax) so reviewers immediately see what their
  // slot baseline & autoscale limit SHOULD be based on usage.
  const dispBase = isOpt2 && !isReturnToOd ? 0 : safeBase;
  const dispMax = safeMax;

  const hasTelemetry =
    sizing != null &&
    sizing.source !== "DEFAULT_NO_TELEMETRY" &&
    (Boolean(sizing.timeline?.length) ||
      sizing.p50_slots_all_minutes != null ||
      sizing.p99_slots_busy_minutes != null ||
      sizing.peak_slots_1min != null);

  const p50 = Number(sizing?.p50_slots_all_minutes ?? 0);
  const p95 = Number(sizing?.p95_slots_all_minutes ?? 0);
  const p50Busy = sizing?.p50_slots_busy_minutes != null ? Number(sizing.p50_slots_busy_minutes) : null;
  const p99Busy = Number(sizing?.p99_slots_busy_minutes ?? 0);
  const peak = Number(sizing?.peak_slots_1min ?? 0);
  const busyPct = sizing?.busy_minutes_pct != null ? Number(sizing.busy_minutes_pct) : null;
  const idlePct = busyPct != null ? Math.max(0, Math.round((100 - busyPct) * 10) / 10) : null;
  const days = Number(sizing?.sizing_window_days || windowDays || 30);

  const realTimeline = sizing?.timeline && sizing.timeline.length >= 2 ? sizing.timeline : null;
  const points = useMemo<CapacityTimelinePoint[]>(() => {
    if (realTimeline) return realTimeline;
    return buildFallbackTimeline(
      days,
      p50,
      p95,
      p50Busy,
      hasTelemetry ? p99Busy : dispMax * 0.85,
      hasTelemetry ? peak : dispMax * 0.92,
      busyPct,
    );
  }, [realTimeline, days, p50, p95, p50Busy, p99Busy, peak, busyPct, hasTelemetry, dispMax]);

  const observedPeak = useMemo(
    () => Math.max(peak, ...points.map((pt) => Number(pt.demand || 0))),
    [peak, points],
  );

  const canZoom = isReservation && cMaxRaw > Math.max(dispMax, observedPeak, 50) * 1.8;
  const extremeRatio = isReservation && cMaxRaw > Math.max(dispMax, observedPeak, 50) * 8;
  const [zoomUsage, setZoomUsage] = useState<boolean>(() => extremeRatio);

  useEffect(() => {
    setZoomUsage(extremeRatio);
  }, [extremeRatio, cMaxRaw, dispMax]);

  // Instant "right out of the gate" sizing verdict
  const verdict = useMemo(() => {
    if (isReservation && cMaxRaw > 0) {
      if (cBase > safeBase || cMaxRaw > safeMax) {
        const unused = Math.max(0, cMaxRaw - safeMax);
        return {
          tone: "rose" as const,
          badge: "OVERSIZED",
          detail:
            cBase > safeBase
              ? `${cBase.toLocaleString()} baseline / ${cMaxRaw.toLocaleString()} max today vs ${safeBase.toLocaleString()} baseline / ${safeMax.toLocaleString()} needed`
              : `${cMaxRaw.toLocaleString()} max cap today vs ${safeMax.toLocaleString()} limit needed (${unused.toLocaleString()} unused slots)`,
        };
      }
      if (safeBase > cBase || safeMax > cMaxRaw) {
        return {
          tone: "amber" as const,
          badge: "UNDERSIZED",
          detail: `Steady load (${fmtSlots(p50)} median slots) overflows ${cBase.toLocaleString()} baseline into PAYG burst`,
        };
      }
      return {
        tone: "emerald" as const,
        badge: "PROPERLY SIZED",
        detail: `Matches ${days}d consumption (${safeBase.toLocaleString()} baseline · ${safeMax.toLocaleString()} limit)`,
      };
    }
    return {
      tone: "sky" as const,
      badge: "SIZED FROM USAGE",
      detail: `${dispBase.toLocaleString()} baseline · ${dispMax.toLocaleString()} autoscale limit (${days}d peak ${fmtSlots(observedPeak)} slots)`,
    };
  }, [isReservation, cMaxRaw, cBase, safeBase, safeMax, p50, days, dispBase, dispMax, observedPeak]);

  const showTodayCap = isReservation && cMaxRaw > 0 && !zoomUsage;
  const yMax = useMemo(() => {
    const rawTop = Math.max(
      dispMax,
      dispBase,
      observedPeak,
      showTodayCap ? cMaxRaw : 0,
      commSlots > 0 && commSlots <= dispMax * 1.5 ? commSlots : 0,
      50,
    );
    const step = rawTop <= 120 ? 25 : 50;
    return Math.ceil((rawTop * 1.16) / step) * step;
  }, [dispMax, dispBase, observedPeak, showTodayCap, cMaxRaw, commSlots]);

  const chartHeight = compact ? 182 : 252;
  const commBand = Math.min(commSlots, dispBase);
  const baseAboveComm = Math.max(0, dispBase - commBand);

  const option = useMemo(() => {
    const axisText = dark ? "#a1a1aa" : "#52525b";
    const splitColor = dark ? "rgba(255,255,255,0.06)" : "rgba(15,23,42,0.07)";
    const axisLineColor = dark ? "rgba(148,163,184,0.28)" : "rgba(71,85,105,0.32)";

    const xLabels = points.map((pt) => pt.t);
    const commSeries = points.map(() => commBand);
    const baseSeries = points.map(() => baseAboveComm);
    const autoSeries = points.map((pt) => {
      const d = Number(pt.demand || 0);
      if (d <= dispBase || dispMax <= dispBase) return 0;
      const step50 = Math.ceil((d - dispBase) / 50) * 50;
      return Math.min(dispMax - dispBase, step50);
    });
    const activeCapOutline = points.map((_, idx) => dispBase + autoSeries[idx]);
    const demandSeries = points.map((pt) => Number(Number(pt.demand || 0).toFixed(1)));

    // Build horizontal reference lines + collision-free right-margin pill offsets
    type RawLine = {
      key: string;
      val: number;
      text: string;
      lineColor: string;
      lineType: "solid" | "dashed" | "dotted";
      lineWidth: number;
      pillBg: string;
      pillBorder: string;
      pillText: string;
    };

    const rawLines: RawLine[] = [
      {
        key: "baseline",
        val: dispBase,
        text: compact ? `Base: ${dispBase.toLocaleString()}` : `Baseline: ${dispBase.toLocaleString()}`,
        lineColor: dark ? "#38bdf8" : "#0284c7",
        lineType: "solid",
        lineWidth: 1.9,
        pillBg: dark ? "rgba(14,165,233,0.18)" : "rgba(224,242,254,0.95)",
        pillBorder: dark ? "rgba(56,189,248,0.45)" : "rgba(2,132,199,0.35)",
        pillText: dark ? "#7dd3fc" : "#0369a1",
      },
      {
        key: "autoscale_limit",
        val: dispMax,
        text: compact ? `Limit: ${dispMax.toLocaleString()}` : `Autoscale limit: ${dispMax.toLocaleString()}`,
        lineColor: dark ? "#10b981" : "#059669",
        lineType: "solid",
        lineWidth: 2.0,
        pillBg: dark ? "rgba(16,185,129,0.18)" : "rgba(209,250,229,0.95)",
        pillBorder: dark ? "rgba(52,211,153,0.45)" : "rgba(5,150,105,0.35)",
        pillText: dark ? "#6ee7b7" : "#047857",
      },
    ];

    if (!compact && commBand > 0 && commBand < dispBase) {
      rawLines.push({
        key: "commitment",
        val: commBand,
        text: `Committed: ${commBand.toLocaleString()}`,
        lineColor: dark ? "#94a3b8" : "#64748b",
        lineType: "dashed",
        lineWidth: 1.3,
        pillBg: dark ? "rgba(148,163,184,0.14)" : "rgba(241,245,249,0.95)",
        pillBorder: dark ? "rgba(148,163,184,0.35)" : "rgba(100,116,139,0.35)",
        pillText: dark ? "#cbd5e1" : "#475569",
      });
    }

    if (showTodayCap && cMaxRaw !== dispMax && cMaxRaw !== dispBase) {
      rawLines.push({
        key: "today_cap",
        val: cMaxRaw,
        text: compact ? `Cap: ${cMaxRaw.toLocaleString()}` : `Today's cap: ${cMaxRaw.toLocaleString()}`,
        lineColor: "#f43f5e",
        lineType: "dashed",
        lineWidth: 1.75,
        pillBg: dark ? "rgba(244,63,94,0.18)" : "rgba(255,228,230,0.95)",
        pillBorder: dark ? "rgba(251,113,133,0.45)" : "rgba(244,63,94,0.35)",
        pillText: dark ? "#fda4af" : "#be123c",
      });
    }

    // Deterministic vertical collision avoidance for right-margin pill badges
    const gridTop = 26;
    const gridBottom = 26;
    const plotH = Math.max(80, chartHeight - gridTop - gridBottom);
    const minGap = compact ? 18 : 21;
    const sorted = [...rawLines].sort((a, b) => a.val - b.val);
    const natYs = sorted.map((l) => plotH * (1 - l.val / Math.max(yMax, 1)));
    const placedYs = [...natYs];

    if (placedYs.length > 0) {
      placedYs[0] = Math.min(placedYs[0], plotH - 2);
      for (let i = 1; i < placedYs.length; i++) {
        if (placedYs[i] > placedYs[i - 1] - minGap) {
          placedYs[i] = placedYs[i - 1] - minGap;
        }
      }
      const last = placedYs.length - 1;
      if (placedYs[last] < 2) {
        placedYs[last] = 2;
        for (let i = last - 1; i >= 0; i--) {
          if (placedYs[i] < placedYs[i + 1] + minGap) {
            placedYs[i] = placedYs[i + 1] + minGap;
          }
        }
      }
    }

    const markLines = sorted.map((l, idx) => ({
      yAxis: l.val,
      lineStyle: { color: l.lineColor, type: l.lineType, width: l.lineWidth },
      label: {
        show: true,
        position: "end",
        distance: 6,
        offset: [0, Math.round(placedYs[idx] - natYs[idx])],
        formatter: l.text,
        color: l.pillText,
        backgroundColor: l.pillBg,
        borderColor: l.pillBorder,
        borderWidth: 1,
        borderRadius: 4,
        padding: compact ? [2, 5] : [3, 6],
        fontFamily: "JetBrains Mono, monospace",
        fontSize: compact ? 9.5 : 10,
        fontWeight: 600,
      },
    }));

    // Shaded oversized unused zone between Autoscale limit and Today's cap
    const showOversizedBand = showTodayCap && cMaxRaw > dispMax;
    const oversizedRatio = showOversizedBand ? (cMaxRaw - dispMax) / Math.max(yMax, 1) : 0;
    const markAreas = showOversizedBand
      ? [
          [
            {
              yAxis: dispMax,
              itemStyle: {
                color: dark ? "rgba(244,63,94,0.065)" : "rgba(244,63,94,0.055)",
              },
              label:
                !compact && oversizedRatio >= 0.22
                  ? {
                      show: true,
                      position: "insideTopLeft",
                      offset: [10, 6],
                      formatter: `Oversized unused headroom: ${(cMaxRaw - dispMax).toLocaleString()} slots above limit`,
                      color: dark ? "rgba(253,164,175,0.85)" : "#be123c",
                      fontFamily: "JetBrains Mono, monospace",
                      fontSize: 10,
                    }
                  : { show: false },
            },
            { yAxis: cMaxRaw },
          ],
        ]
      : undefined;

    const tickStep = Math.max(1, Math.round(points.length / (compact ? 4 : 6)));

    return {
      animationDuration: 350,
      textStyle: { fontFamily: "DM Sans, sans-serif", color: axisText },
      tooltip: {
        trigger: "axis",
        axisPointer: {
          type: "line",
          lineStyle: { color: dark ? "rgba(255,255,255,0.22)" : "rgba(15,23,42,0.22)", type: "dashed" },
        },
        confine: true,
        backgroundColor: dark ? "rgba(12,12,15,0.96)" : "rgba(255,255,255,0.98)",
        borderColor: dark ? "#27272a" : "#e2e8f0",
        textStyle: { color: dark ? "#fafafa" : "#0f172a", fontFamily: "DM Sans, sans-serif", fontSize: 12 },
        formatter: (params: any) => {
          const arr = Array.isArray(params) ? params : [params];
          const idx = arr[0]?.dataIndex ?? 0;
          const pt = points[idx];
          if (!pt) return "";
          const dem = Number(pt.demand || 0);
          const autoStep = autoSeries[idx] || 0;
          const activeSlots = dispBase + autoStep;
          const rows: string[] = [
            `<div style="display:flex;justify-content:space-between;gap:14px"><span style="color:#f59e0b">● Slot demand</span><b style="font-family:JetBrains Mono,monospace">${fmtSlots(dem)} slots</b></div>`,
          ];
          if (pt.med_busy != null && pt.med_busy > 0 && dem > 0) {
            rows.push(
              `<div style="display:flex;justify-content:space-between;gap:14px;color:${dark ? "#a1a1aa" : "#64748b"};font-size:11px"><span>&nbsp;&nbsp;&nbsp;Median active${pt.busy_mins ? ` (${pt.busy_mins}m)` : ""}</span><span style="font-family:JetBrains Mono,monospace">${fmtSlots(pt.med_busy)} slots</span></div>`,
            );
          }
          rows.push(
            `<div style="display:flex;justify-content:space-between;gap:14px"><span style="color:#38bdf8">■ Baseline</span><b style="font-family:JetBrains Mono,monospace">${dispBase.toLocaleString()} slots</b></div>`,
          );
          rows.push(
            `<div style="display:flex;justify-content:space-between;gap:14px"><span style="color:#3b82f6">■ Autoscale step</span><b style="font-family:JetBrains Mono,monospace">+${autoStep.toLocaleString()} slots</b></div>`,
          );
          rows.push(
            `<div style="display:flex;justify-content:space-between;gap:14px;border-top:1px solid ${dark ? "#27272a" : "#e2e8f0"};padding-top:4px;margin-top:3px"><span style="color:#10b981">Autoscale limit</span><b style="font-family:JetBrains Mono,monospace">${activeSlots.toLocaleString()} / ${dispMax.toLocaleString()} slots</b></div>`,
          );
          if (isReservation && cMaxRaw > 0) {
            rows.push(
              `<div style="display:flex;justify-content:space-between;gap:14px;color:#fb7185"><span>Today's cap</span><b style="font-family:JetBrains Mono,monospace">${cMaxRaw.toLocaleString()} slots</b></div>`,
            );
          }
          return (
            `<div style="min-width:215px;line-height:1.5">` +
            `<div style="font-weight:600;margin-bottom:4px">${fmtBucketTooltip(pt.t)}${!realTimeline ? " · modeled" : ""}</div>` +
            rows.join("") +
            `</div>`
          );
        },
      },
      grid: {
        left: 38,
        right: compact ? 84 : 138,
        top: gridTop,
        bottom: gridBottom,
        containLabel: false,
      },
      xAxis: {
        type: "category",
        data: xLabels,
        boundaryGap: true,
        axisTick: { show: false },
        axisLine: { show: true, lineStyle: { color: axisLineColor } },
        axisLabel: {
          color: axisText,
          fontSize: compact ? 9.5 : 10,
          margin: 7,
          formatter: (val: string) => fmtBucketAxis(val),
          interval: (idx: number) => idx === 0 || idx === points.length - 1 || idx % tickStep === 0,
          hideOverlap: true,
        },
      },
      yAxis: {
        type: "value",
        name: "Slots",
        nameLocation: "end",
        nameGap: 8,
        nameTextStyle: {
          color: dark ? "#93c5fd" : "#0369a1",
          fontSize: compact ? 10 : 11,
          fontWeight: 600,
          align: "right",
        },
        min: 0,
        max: yMax,
        splitNumber: compact ? 3 : 4,
        axisLine: { show: true, lineStyle: { color: axisLineColor } },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: splitColor } },
        axisLabel: {
          color: axisText,
          fontSize: compact ? 9.5 : 10,
          fontFamily: "JetBrains Mono, monospace",
        },
      },
      series: [
        // 1. Committed Slots (diagonal hatch band, 0 -> commBand)
        {
          name: "Committed Slots",
          type: "bar",
          stack: "cap_bands",
          barWidth: "101.5%",
          barCategoryGap: "0%",
          silent: true,
          z: 1,
          itemStyle: {
            color: dark ? "rgba(56,189,248,0.16)" : "rgba(186,230,253,0.65)",
            decal:
              commBand > 0
                ? {
                    symbol: "rect",
                    dashArrayX: [1, 0],
                    dashArrayY: [2, 5],
                    rotation: Math.PI / 4,
                    color: dark ? "rgba(148,163,184,0.35)" : "rgba(71,85,105,0.25)",
                  }
                : undefined,
          },
          data: commSeries,
        },
        // 2. Baseline Slots (flat continuous sky-blue band, commBand -> dispBase)
        {
          name: "Baseline Slots",
          type: "bar",
          stack: "cap_bands",
          barWidth: "101.5%",
          barCategoryGap: "0%",
          silent: true,
          z: 2,
          itemStyle: {
            color: dark ? "rgba(56,189,248,0.22)" : "rgba(186,230,253,0.72)",
          },
          data: baseSeries,
        },
        // 3. Stepped Autoscaling Slots (50-slot rectangular plateaus above Baseline)
        {
          name: "Autoscaling Slots",
          type: "bar",
          stack: "cap_bands",
          barWidth: "101.5%",
          barCategoryGap: "0%",
          silent: true,
          z: 3,
          itemStyle: {
            color: isReturnToOd
              ? dark
                ? "rgba(59,130,246,0.24)"
                : "rgba(59,130,246,0.18)"
              : dark
                ? "rgba(59,130,246,0.44)"
                : "rgba(59,130,246,0.34)",
          },
          data: autoSeries,
        },
        // 4. Crisp architectural stepped outline tracing active slot allocation
        {
          name: "Allocated Step",
          type: "line",
          step: "middle",
          symbol: "none",
          silent: true,
          z: 5,
          lineStyle: {
            color: dark ? "rgba(96,165,250,0.7)" : "rgba(37,99,235,0.62)",
            width: 1.2,
            type: isReturnToOd ? "dashed" : "solid",
          },
          data: activeCapOutline,
        },
        // 5. Smooth glowing amber Slot Demand curve + horizontal reference lines
        {
          name: "Demand",
          type: "line",
          smooth: 0.34,
          showSymbol: false,
          symbol: "circle",
          symbolSize: 5,
          z: 12,
          itemStyle: { color: "#f59e0b" },
          lineStyle: {
            color: "#f59e0b",
            width: compact ? 2.3 : 2.75,
            shadowColor: "rgba(245,158,11,0.32)",
            shadowBlur: 7,
          },
          areaStyle: {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: dark ? "rgba(245,158,11,0.16)" : "rgba(245,158,11,0.14)" },
                { offset: 1, color: "rgba(245,158,11,0.0)" },
              ],
            },
          },
          data: demandSeries,
          markArea: markAreas
            ? {
                silent: true,
                animation: false,
                data: markAreas,
              }
            : undefined,
          markLine: {
            symbol: ["none", "none"],
            silent: true,
            animation: false,
            data: markLines,
          },
        },
      ],
    };
  }, [
    dark,
    points,
    commBand,
    baseAboveComm,
    dispBase,
    dispMax,
    compact,
    showTodayCap,
    cMaxRaw,
    chartHeight,
    yMax,
    isReturnToOd,
    isReservation,
    realTimeline,
  ]);

  const verdictStyle =
    verdict.tone === "rose"
      ? "border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-300"
      : verdict.tone === "amber"
        ? "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-300"
        : verdict.tone === "emerald"
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300"
          : "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-300";

  const dotStyle =
    verdict.tone === "rose"
      ? "bg-rose-500"
      : verdict.tone === "amber"
        ? "bg-amber-500"
        : verdict.tone === "emerald"
          ? "bg-emerald-500"
          : "bg-sky-500";

  return (
    <div className={compact ? "space-y-1.5" : "space-y-3 rounded-lg border border-zinc-200 p-3.5 dark:border-ink-800"}>
      {/* Instant Sizing Verdict + Optional Zoom Toggle */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold tracking-wide",
              verdictStyle,
            )}
          >
            <span className={cn("h-1.5 w-1.5 rounded-full", dotStyle)} />
            {verdict.badge}
          </span>
          {!compact && <span className="truncate text-[11px] text-zinc-600 dark:text-zinc-300">{verdict.detail}</span>}
        </div>

        <div className="flex items-center gap-2">
          {canZoom && (
            <div className="inline-flex rounded-md border border-zinc-200 bg-zinc-100 p-0.5 text-[10px] font-medium dark:border-ink-800 dark:bg-[#0c0c0f]">
              <button
                type="button"
                onClick={() => setZoomUsage(false)}
                className={cn(
                  "rounded px-1.5 py-0.5 transition-colors",
                  !zoomUsage
                    ? "bg-white text-rose-600 shadow-sm dark:bg-zinc-800 dark:text-rose-300"
                    : "text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200",
                )}
              >
                {compact ? `${cMaxRaw} cap` : `Full scale (${cMaxRaw} cap)`}
              </button>
              <button
                type="button"
                onClick={() => setZoomUsage(true)}
                className={cn(
                  "rounded px-1.5 py-0.5 transition-colors",
                  zoomUsage
                    ? "bg-white text-sky-600 shadow-sm dark:bg-zinc-800 dark:text-sky-300"
                    : "text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200",
                )}
              >
                Zoom usage
              </button>
            </div>
          )}
          {!compact && (
            <span className="font-mono text-[10px] text-zinc-500">
              {days}d window{busyPct != null ? ` · ${busyPct}% active / ${idlePct}% idle` : ""}
            </span>
          )}
        </div>
      </div>

      {/* Legend Swatches (matching GCP Autoscaling Architecture visual vocabulary) */}
      {!compact && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-zinc-500 dark:text-zinc-400">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-[3px] w-4 rounded-full bg-amber-500" />
            Demand
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-3.5 rounded-[2px] border border-blue-500/60 bg-blue-500/40" />
            Autoscaling Slots
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-3.5 rounded-[2px] border border-sky-400/50 bg-sky-400/25" />
            Baseline Slots ({dispBase.toLocaleString()})
          </span>
          {commBand > 0 && (
            <span className="inline-flex items-center gap-1.5">
              <span
                className="h-2.5 w-3.5 rounded-[2px] border border-slate-400/50"
                style={{
                  backgroundImage:
                    "repeating-linear-gradient(-45deg, rgba(148,163,184,0.38) 0 2px, transparent 2px 5px)",
                }}
              />
              Committed Slots ({commBand.toLocaleString()})
            </span>
          )}
          <span className="inline-flex items-center gap-1.5">
            <span className="h-[2px] w-3.5 bg-emerald-500" />
            Autoscale limit ({dispMax.toLocaleString()})
          </span>
          {isReservation && cMaxRaw > 0 && cMaxRaw !== dispMax && (
            <span className="inline-flex items-center gap-1.5">
              <span className="w-3.5 border-t-2 border-dashed border-rose-500" />
              Today&apos;s cap ({cMaxRaw.toLocaleString()})
            </span>
          )}
        </div>
      )}

      <ReactECharts option={option} style={{ height: chartHeight }} notMerge />

      {!hasTelemetry && !compact && (
        <div className="rounded-md bg-amber-500/10 px-2.5 py-1.5 text-[11px] text-amber-400">
          Minute-level <code>JOBS_TIMELINE</code> history was not recorded on this card yet — run a fresh scan in{" "}
          <b>Settings</b> to plot live per-bucket slot consumption.
        </div>
      )}

      {!compact && (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <div className="rounded-md border border-sky-500/25 bg-sky-500/[0.04] p-2.5 text-xs">
            <div className="flex items-center justify-between gap-2 font-medium text-sky-600 dark:text-sky-300">
              <span>Baseline line: {dispBase.toLocaleString()} slots</span>
              <span className="rounded bg-sky-500/15 px-1.5 py-0.5 font-mono text-[10px]">
                {isOpt2 && !isReturnToOd ? "Opt 2 = 0" : `p50 = ${fmtSlots(p50)} → ${safeBase}`}
              </span>
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">
              {isOpt2 && !isReturnToOd ? (
                <>
                  Option 2 sets <b>0 baseline slots</b> so you pay <b>$0/hr</b> during the{" "}
                  {idlePct != null ? `${idlePct}% of minutes that sit idle` : "idle hours & weekends"}.
                  {safeBase > 0 ? ` (Option 1 sizes ${safeBase} steady baseline slots from p50 = ${fmtSlots(p50)}.)` : ""}
                </>
              ) : safeBase === 0 ? (
                <>
                  Median minute (<code>p50</code>) across the {days}d window is <b>{fmtSlots(p50)} slots</b>
                  {idlePct != null ? ` (${idlePct}% of minutes are idle)` : ""}. Rounding down to 50 sets{" "}
                  <b>Baseline = 0 slots</b> so you never pay 24/7 for empty capacity.
                </>
              ) : (
                <>
                  Median minute (<code>p50</code>) across all {days}d clock minutes is <b>{fmtSlots(p50)} slots</b>.
                  Rounding down to BigQuery&apos;s 50-slot step sets <b>Baseline = {safeBase} slots</b> that stay busy &ge;50% of the time.
                </>
              )}
            </p>
          </div>

          <div className="rounded-md border border-emerald-500/25 bg-emerald-500/[0.04] p-2.5 text-xs">
            <div className="flex items-center justify-between gap-2 font-medium text-emerald-600 dark:text-emerald-300">
              <span>Autoscale limit line: {dispMax.toLocaleString()} slots</span>
              <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 font-mono text-[10px]">
                {`p99 = ${fmtSlots(p99Busy)} → ${safeMax}`}
              </span>
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">
              99% of active query minutes fit inside <b>&le; {fmtSlots(p99Busy)} slots</b>
              {observedPeak > 0 ? ` (peak: ${fmtSlots(observedPeak)} slots)` : ""}, so rounding up to a 50-slot step sets{" "}
              <b>Autoscale limit = {safeMax} slots</b> (+{recAutoAdd} burst above {safeBase} baseline).
              {isReturnToOd && (
                <>
                  {" "}
                  <span className="font-medium text-emerald-600 dark:text-emerald-300">
                    Option 2 skips even a 50-slot pool because On-Demand ($6.25/TiB
                    {bytesTib30d != null ? ` · ${bytesTib30d.toLocaleString()} TiB/mo` : ""}) costs less.
                  </span>
                </>
              )}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
