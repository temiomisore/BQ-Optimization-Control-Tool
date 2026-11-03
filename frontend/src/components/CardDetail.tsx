import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle, BarChart3, Calculator, CheckCircle2, ChevronRight, ExternalLink, GitPullRequest, History, Landmark, ShieldCheck, Users, X, XCircle,
} from "lucide-react";
import type { CapacitySizing, ChangeSet, Dashboard, SavingsMath } from "@/lib/api";
import { useDecide } from "@/lib/store";
import {
  CLASS_META, cn, ddlFor, fmtDate, fullPath, isProperlySized, isW01, isW02, isW03, needsTwo, safeConsoleUrl, targetLabel, titleize, usd,
} from "@/lib/utils";
import { AnimatedNumber, Badge, Button, Dialog, Meter } from "./ui";
import { CodeBlock, CopyButton } from "./Code";
import { Rich, StateCompare, type StateCompareItem } from "./StateCompare";
import { RegionBadge } from "./RegionBadge";
import { SlotSizingChart } from "./SlotSizingChart";

const num = (v: unknown) => Number(v || 0);

const pctTxt = (v: unknown) => `${Math.round(Number(v || 0) * 1000) / 10}%`;

/** "How this saving is calculated": renders evidence.savings_math so every number shows its work. */
function SavingsMathPanel({ sm, net, gross, recurring }: { sm?: SavingsMath; net: number; gross: number; recurring: number }) {
  if (!sm) {
    return (
      <div className="rounded-lg border border-zinc-200 p-3 text-xs text-zinc-500 dark:border-ink-800">
        Legacy estimate: this card was priced before billing-aware savings (every job at $/TiB). The next rules run
        re-prices it, or retires it if the finding no longer applies. Treat the number as an upper bound.
      </div>
    );
  }
  const rows: [string, React.ReactNode][] = [];
  if (sm.billing_mix) rows.push(["Billing mix", sm.billing_mix]);
  if (sm.attributed_monthly_usd != null)
    rows.push(["Spend this card acts on", `${usd(sm.attributed_monthly_usd, 2)}/mo`
      + (Number(sm.reservation_monthly_usd || 0) > 0
        ? ` (${usd(sm.on_demand_monthly_usd, 2)} on-demand + ${usd(sm.reservation_monthly_usd, 2)} reservation)` : "")]);
  if (sm.reduction != null) rows.push(["Assumed reduction", pctTxt(sm.reduction)]);
  if (sm.reservation_realization != null && Number(sm.reservation_monthly_usd || 0) > 0)
    rows.push(["Reservation realization", `${pctTxt(sm.reservation_realization)} of slot savings become cash`]);
  if (sm.split_rule) rows.push(["Shared jobs", sm.split_rule]);
  if (sm.window_note) rows.push(["Window", sm.window_note]);
  if (sm.pool_key) rows.push(["Overlap pool", `${sm.pool_key} — compounded with other cards on the same spend in the headline total`]);
  if (sm.cost_source && sm.cost_source !== "BILLING_AWARE") rows.push(["Cost source", titleize(String(sm.cost_source).toLowerCase())]);
  if (recurring > 0) rows.push(["Net of running cost", `${usd(gross, 2)} − ${usd(recurring, 2)} running cost = ${usd(net, 2)}/mo`]);
  return (
    <div className="space-y-2 rounded-lg border border-zinc-200 p-3 text-xs dark:border-ink-800">
      {sm.formula && <div className="font-mono text-[11px] leading-relaxed text-zinc-700 dark:text-zinc-300">{sm.formula}</div>}
      {rows.length > 0 && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-zinc-500">{k}</dt>
              <dd className="text-zinc-700 dark:text-zinc-300">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {sm.cap_applied && <div className="text-amber-400">Capped: {sm.cap_applied}</div>}
      {sm.demo_floor_applied && (
        <div className="text-amber-400">
          DEMO: a synthetic minimum{sm.demo_floor_usd != null ? ` of ${usd(sm.demo_floor_usd, 2)}` : ""} replaced the measured
          {sm.measured_gross_usd != null ? ` ${usd(sm.measured_gross_usd, 2)}` : " value"}
          {sm.demo_floor_inputs?.length ? ` (${sm.demo_floor_inputs.join(", ")})` : ""}. Never shown when demo_mode is off.
        </div>
      )}
      {sm.repriced_from_usd != null && (
        <div className="text-zinc-500">Re-priced from the legacy estimate of {usd(sm.repriced_from_usd, 2)}/mo.</div>
      )}
    </div>
  );
}

function Section({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="label flex items-center gap-1.5">{icon}{title}</h4>
      {children}
    </section>
  );
}

export function CardDetail({
  c,
  data,
  onClose,
  onPr,
}: {
  c: ChangeSet;
  data: Dashboard;
  onClose: () => void;
  onPr: (c: ChangeSet) => void;
}) {
  const decide = useDecide();
  const w01 = isW01(c);
  const w02 = isW02(c);
  const w03 = isW03(c);
  const wCap = w01 || w03;
  const propSized = isProperlySized(c);
  const ev = c.evidence || {};
  const pr = c.proposed || {};
  const estimatorUrl = safeConsoleUrl(ev.slot_estimator_url);
  const [opt, setOpt] = useState<number>(Number(pr.selected_option) || 1);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState(data.reasons[0] || "OTHER");
  const [note, setNote] = useState("");
  useEffect(() => {
    setOpt(Number(pr.selected_option) || 1);
    setRejectOpen(false);
  }, [c.change_set_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const meta = CLASS_META[c.apply_class] || CLASS_META[1];
  const hasOpt2 = !!pr.generated_ddl_option2;
  const recOpt = wCap ? (Number(ev.recommended_option || pr.selected_option || 1) === 2 ? 2 : 1) : 1;
  // Always display the Recommended winner first on the left as "Option 1" and the runner-up on the
  // right as "Option 2", while `opt` (1 | 2) preserves the backend execution plan ID for `/api/decide`.
  const orderedPlans: [number, number] = wCap && !propSized && recOpt === 2 ? [2, 1] : [1, 2];
  const p1Rank: 1 | 2 = orderedPlans[0] === 1 ? 1 : 2;
  const p2Rank: 1 | 2 = orderedPlans[0] === 2 ? 1 : 2;
  const activeRank: 1 | 2 = orderedPlans[0] === opt ? 1 : 2;
  const headline = propSized
    ? 0
    : wCap
      ? num(opt === 2 ? ev.option_2_savings_usd : ev.option_1_savings_usd) || num(c.net_monthly_value_usd)
      : num(c.net_monthly_value_usd);
  const rawDdl = hasOpt2 && opt === 2 ? pr.generated_ddl_option2 || "" : ddlFor(c);
  const ddl = wCap && !propSized ? rawDdl.replace(/^(\s*--\s*OPTION\s+)[12]\b/i, `$1${activeRank}`) : rawDdl;
  const isPr = c.apply_class === 4 || c.execution_route === "CI_PULL_REQUEST";
  const approvals = c.approvals || [];
  const two = needsTwo(c);
  const approveLabel = two
    ? approvals.length === 0
      ? "Approve (Sign-off 1/2: Data Owner)"
      : "Sign-off 2/2: Platform"
    : isPr
      ? "Approve for PR hand-off"
      : "Approve Change";
  const stripOptPrefix = (s: string) => s.replace(/^Option\s*[12]\s*[·:—-]\s*/i, "").trim();
  const planTitleFor = (planId: number, displayRank: number): string => {
    if (w02) {
      return planId === 1 ? "Option 1: 50-Slot Human Sandbox + 50 GB Cap" : "Option 2: Audit SQL";
    }
    const raw =
      planId === 1
        ? String(ev.option_1_label || "Baseline + Autoscale")
        : String(ev.option_2_label || "0-Baseline Autoscale");
    return `Option ${displayRank} · ${stripOptPrefix(raw)}`;
  };

  const bullets = useMemo(() => {
    const scanDays = Number(ev.scan_window_days || ev.capacity_sizing?.scan_window_days || ev.sizing_window_days || 30);
    const activeDays = Number(ev.observed_active_days ?? ev.capacity_sizing?.observed_active_days ?? ev.sizing_window_days ?? scanDays);
    const normDays = Number(ev.sizing_window_days || 30);
    const hasYoungSpan = activeDays > 0 && scanDays > activeDays + 5;
    if (w01) {
      const base = pr.recommended_baseline_slots ?? data.w01?.baseline ?? 0;
      const max = pr.recommended_autoscale_max_slots ?? data.w01?.max ?? 50;
      const src = ev.capacity_sizing?.source || ev.capacity_sizing?.method;
      const winNote = hasYoungSpan ? `${scanDays}d scan · ${activeDays}d active` : `${scanDays}d history`;
      const opt1Tag = num(base) === 0 ? "0-baseline PAYG" : "1-yr commit target";
      const sizedLine = hasYoungSpan
        ? `Scanned ${scanDays}d INFORMATION_SCHEMA history (${activeDays}d active${activeDays < normDays ? `, normalized over ${normDays}d` : ""}): baseline ${base} · max ${max} slots${src ? ` (${src})` : ""}.`
        : `Sized from ${scanDays}d historical slot telemetry: baseline ${base} · max ${max} slots${src ? ` (${src})` : ""}.`;
      const p1Line = `(${opt1Tag}): ${usd(ev.option_1_monthly_cost_usd)}/mo → est. ${usd(ev.option_1_savings_usd)}/mo efficiency gain.`;
      const p2Line = `(0-baseline autoscale): ${usd(ev.option_2_monthly_cost_usd)}/mo → est. ${usd(ev.option_2_savings_usd)}/mo efficiency gain.`;
      const firstLine = recOpt === 2 ? p2Line : p1Line;
      const secondLine = recOpt === 2 ? p1Line : p2Line;
      return [
        `On-demand today: ${usd(ev.on_demand_spend_monthly)}/mo for ${num(ev.bytes_scanned_tib_30d).toLocaleString()} TiB/mo (${winNote}).`,
        `Option 1 — Recommended ${firstLine}`,
        `Option 2 — Alternative ${secondLine}`,
        sizedLine,
      ];
    }
    if (w03) {
      const curBase = ev.current_baseline_slots ?? pr.current_baseline_slots ?? 0;
      const curAuto = ev.current_autoscale_add_slots ?? pr.current_autoscale_max_slots ?? 0;
      const recBase = pr.recommended_baseline_slots ?? 0;
      const recMax = pr.recommended_autoscale_max_slots ?? 0;
      const src = ev.capacity_sizing?.source;
      const sizedLine = hasYoungSpan
        ? `Scanned ${scanDays}d INFORMATION_SCHEMA history — reservation active for ${activeDays}d${activeDays < normDays ? `, normalized over ${normDays}d` : ""}${src ? ` (${src})` : ""}.`
        : `${propSized ? "Verified" : "Sized"} from ${scanDays}d historical slot telemetry${src ? ` (${src})` : ""}.`;
      if (propSized) {
        const p50 = ev.capacity_sizing?.p50_slots_all_minutes ?? 0;
        const p99 = ev.capacity_sizing?.p99_slots_busy_minutes ?? 0;
        return [
          `Current ${ev.edition || "ENTERPRISE"} reservation '${ev.reservation_name || c.target_table}': ${curBase} baseline + ${curAuto} autoscale slots (${num(curBase) + num(curAuto)} max · ${usd(ev.current_reservation_spend_monthly)}/mo).`,
          `Observed slot consumption (median ${p50} slots · p99 busy ${p99} slots) aligns with your provisioned baseline & autoscale limit.`,
          `No capacity resize or billing migration is needed ($0/mo change).`,
          sizedLine,
        ];
      }
      const opt2Mode = ev.option_2_mode === "ON_DEMAND" ? "Return to On-Demand ($6.25/TiB)" : `0-baseline autoscale (0 → ${recMax})`;
      const p1Line = `(${recBase} baseline → ${recMax} max): ${usd(ev.option_1_monthly_cost_usd)}/mo → est. ${usd(ev.option_1_savings_usd)}/mo efficiency gain.`;
      const p2Line = `(${opt2Mode}): ${usd(ev.option_2_monthly_cost_usd)}/mo → est. ${usd(ev.option_2_savings_usd)}/mo efficiency gain.`;
      const firstLine = recOpt === 2 ? p2Line : p1Line;
      const secondLine = recOpt === 2 ? p1Line : p2Line;
      return [
        `Current ${ev.edition || "ENTERPRISE"} reservation '${ev.reservation_name || c.target_table}': ${curBase} baseline + ${curAuto} autoscale slots (${usd(ev.current_reservation_spend_monthly)}/mo).`,
        `Option 1 — Recommended ${firstLine}`,
        `Option 2 — Alternative ${secondLine}`,
        sizedLine,
      ];
    }
    if (w02) {
      return [
        `${num(ev.human_adhoc_queries_30d).toLocaleString()} human ad-hoc queries scanned ${num(ev.human_adhoc_tib_30d).toFixed(1)} TiB (30d).`,
        `Cap humans at ${ev.recommended_human_query_cap_gib ?? pr.human_max_bytes_billed_gib} GiB per query.`,
        `Service accounts & ETL are 100% exempt (${num(ev.exempt_service_account_queries_30d).toLocaleString()} queries untouched).`,
      ];
    }
    const out: string[] = [];
    if (c.finding_summary) out.push(c.finding_summary);
    if (ev.pattern_matched) out.push(`Pattern: ${ev.pattern_matched}`);
    if (ev.gemini_ai_diagnosis) out.push(`Gemini diagnosis: ${ev.gemini_ai_diagnosis}`);
    if (ev.dry_run_verification) out.push(`Dry run: ${ev.dry_run_verification}`);
    return out;
  }, [c, w01, w02, w03, propSized, ev, pr, recOpt, data.w01]);

  const comparePanels = useMemo<{ before: unknown; after: unknown }>(() => {
    const cs = (ev.current_state || {}) as Record<string, unknown>;
    const ps = (ev.proposed_state || {}) as Record<string, unknown>;
    if (!wCap || propSized) {
      return { before: ev.current_state, after: ev.proposed_state };
    }

    if (w03) {
      const beforeItems: StateCompareItem[] = [];
      if (cs.reservation) beforeItems.push({ label: "Reservation", value: cs.reservation });
      if (cs.provisioned_capacity) beforeItems.push({ label: "Provisioned capacity", value: cs.provisioned_capacity });
      if (cs.observed_demand) beforeItems.push({ label: "Observed demand", value: cs.observed_demand });
      if (cs.commitment_coverage) beforeItems.push({ label: "Commitment coverage", value: cs.commitment_coverage });
      if (cs.current_monthly_bill) beforeItems.push({ label: "Current monthly bill", value: cs.current_monthly_bill });
      if (cs.cost_driver) beforeItems.push({ label: "Cost driver", value: cs.cost_driver });

      const recBase = num(pr.recommended_baseline_slots);
      const recMax = num(pr.recommended_autoscale_max_slots);
      const recAutoAdd = Math.max(0, recMax - recBase);
      const curMax = num(
        ev.current_max_slots ??
          num(ev.current_baseline_slots ?? pr.current_baseline_slots) +
            num(ev.current_autoscale_add_slots ?? pr.current_autoscale_max_slots),
      );
      const curBill = num(ev.current_reservation_spend_monthly || ev.on_demand_spend_monthly);
      const opt1Cost = num(ev.option_1_monthly_cost_usd);
      const opt1Sav = num(ev.option_1_savings_usd);
      const opt1Pct = curBill > 0 ? Math.round((opt1Sav / curBill) * 100) : 0;
      const opt2Cost = num(ev.option_2_monthly_cost_usd);
      const opt2Sav = num(ev.option_2_savings_usd);
      const opt2Pct = curBill > 0 ? Math.round((opt2Sav / curBill) * 100) : 0;
      const ed = String(ev.edition || pr.edition || "ENTERPRISE").toUpperCase();
      const covBase = num(ev.covered_baseline_slots);
      const commSlots = ev.committed_slots_edition;

      let opt1Note = "1-Yr Commit target rate; note CUD lock-in";
      if (commSlots && recBase < covBase && ed !== "STANDARD") {
        opt1Note = "note active CUD lock-in until expiry";
      } else if (ed === "STANDARD") {
        opt1Note = "Standard PAYG";
      } else if (recBase === 0) {
        opt1Note =
          curMax > recMax
            ? `caps burst ${curMax} → ${recMax} slots · 0-baseline PAYG, no CUD lock-in`
            : "0-baseline PAYG autoscale · no CUD lock-in";
      }

      const opt1Plan = String(
        (recOpt === 1 ? ps.recommended_option_1 : undefined) ||
          ps.option_1_rightsized ||
          `${recBase} Baseline Slots + up to ${recAutoAdd} Autoscaling Slots = ${usd(opt1Cost, 2)} / month`,
      );
      const opt1SavTxt = `${usd(opt1Sav, 2)} / month (${opt1Pct}% efficiency gain · ${opt1Note})`;

      const isOdMode = ev.option_2_mode === "ON_DEMAND";
      const opt2Plan = String(
        (recOpt === 2 ? ps.recommended_option_1 || ps.recommended_option_2 : undefined) ||
          ps.option_2_alternative ||
          (isOdMode
            ? `Unassign QUERY workloads → On-Demand (${num(ev.bytes_scanned_tib_30d).toFixed(1)} TiB/mo @ $6.25/TiB) + zero '${ev.reservation_name || c.target_table}' capacity = ${usd(opt2Cost, 2)} / month`
            : `0 Baseline Slots ($0 fixed) + Pure Autoscaling up to ${recMax} Slots = ${usd(opt2Cost, 2)} / month`),
      );
      const opt2SavTxt = isOdMode
        ? `${usd(opt2Sav, 2)} / month (${opt2Pct}% efficiency gain · pay per TiB scanned, $0 idle slots)`
        : `${usd(opt2Sav, 2)} / month (${opt2Pct}% efficiency gain · $0 idle cost overnight & no lock-in)`;

      const afterItems: StateCompareItem[] =
        opt === 2
          ? [
              {
                label: `Option ${p2Rank} · ${isOdMode ? "Return to On-Demand" : "0-Baseline Autoscale"}`,
                badge: recOpt === 2 ? "Recommended" : "Selected",
                badgeTone: recOpt === 2 ? "emerald" : "sky",
                value: opt2Plan,
              },
              { label: "Projected net savings", value: opt2SavTxt },
              {
                label: recOpt === 1 ? "Recommended · Option 1" : `Fallback if staying on slots · Option ${p1Rank}`,
                badge: recOpt === 1 ? "Saves more" : undefined,
                badgeTone: "emerald",
                muted: recOpt !== 1,
                value: `${recBase} Baseline + ${recAutoAdd} Autoscale (${recMax} max) → ${usd(opt1Cost, 2)}/mo (${usd(opt1Sav, 2)}/mo savings · ${opt1Note})`,
              },
            ]
          : [
              {
                label: `Option ${p1Rank} · Right-size reservation (${recBase} + ${recAutoAdd} slots)`,
                badge: recOpt === 1 ? "Recommended" : "Selected",
                badgeTone: recOpt === 1 ? "emerald" : "sky",
                value: opt1Plan,
              },
              { label: "Projected net savings", value: opt1SavTxt },
              {
                label: recOpt === 2 ? "Recommended · Option 1" : `Alternative · Option ${p2Rank}`,
                badge: recOpt === 2 ? "Saves more" : undefined,
                badgeTone: "emerald",
                muted: recOpt !== 2,
                value: `${isOdMode ? "Return to On-Demand" : `0-Baseline Autoscale (0 → ${recMax})`} → ${usd(opt2Cost, 2)}/mo (saves ${usd(opt2Sav, 2)}/mo · ${opt2Pct}% efficiency gain)`,
              },
            ];

      return { before: beforeItems.length ? beforeItems : ev.current_state, after: afterItems };
    }

    // W-01: On-Demand -> Enterprise Edition Slots
    const beforeW01: StateCompareItem[] = [];
    if (cs.billing_model) beforeW01.push({ label: "Current billing model", value: cs.billing_model });
    if (cs["30d_bytes_scanned"]) beforeW01.push({ label: "Observed scan volume", value: cs["30d_bytes_scanned"] });
    if (cs.already_on_reservation) beforeW01.push({ label: "Already on reservation", value: cs.already_on_reservation });
    if (cs.current_monthly_bill) beforeW01.push({ label: "Current monthly bill", value: cs.current_monthly_bill });
    if (cs.cost_driver) beforeW01.push({ label: "Cost driver", value: cs.cost_driver });

    const recBase = num(pr.recommended_baseline_slots ?? data.w01?.baseline);
    const recMax = num(pr.recommended_autoscale_max_slots ?? data.w01?.max);
    const recAutoAdd = Math.max(0, recMax - recBase);
    const odBill = num(ev.on_demand_spend_monthly);
    const opt1Cost = num(ev.option_1_monthly_cost_usd);
    const opt1Sav = num(ev.option_1_savings_usd);
    const opt1Pct = odBill > 0 ? Math.round((opt1Sav / odBill) * 100) : 0;
    const opt2Cost = num(ev.option_2_monthly_cost_usd);
    const opt2Sav = num(ev.option_2_savings_usd);
    const opt2Pct = odBill > 0 ? Math.round((opt2Sav / odBill) * 100) : 0;
    const opt1RateNote = recBase === 0 ? "0-baseline PAYG autoscale · no CUD lock-in" : "1-Yr Commit rate; note commitment lock-in";
    const opt1Plan = String(
      (recOpt === 1 ? ps.recommended_option_1 : undefined) ||
        ps.option_1_steady_24x7 ||
        `${recBase} Baseline Slots + up to ${recAutoAdd} Autoscaling Slots = ${usd(opt1Cost, 2)} / month`,
    );
    const opt2Plan = String(
      (recOpt === 2 ? ps.recommended_option_1 || ps.recommended_option_2 : undefined) ||
        ps.option_2_spiky_0_baseline ||
        `0 Baseline Slots ($0 fixed) + Pure Autoscaling up to ${recMax} Slots = ${usd(opt2Cost, 2)} / month`,
    );

    const afterW01: StateCompareItem[] =
      opt === 2
        ? [
            {
              label: `Option ${p2Rank} · 0-Baseline Pure Autoscale (0 → ${recMax})`,
              badge: recOpt === 2 ? "Recommended" : "Selected",
              badgeTone: recOpt === 2 ? "emerald" : "sky",
              value: opt2Plan,
            },
            {
              label: "Projected net savings",
              value: `${usd(opt2Sav, 2)} / month (${opt2Pct}% efficiency gain · $0 idle cost overnight & no lock-in)`,
            },
            {
              label: recOpt === 1 ? "Recommended · Option 1" : `Alternative · Option ${p1Rank} (Steady 24/7)`,
              badge: recOpt === 1 ? "Saves more" : undefined,
              badgeTone: "emerald",
              muted: recOpt !== 1,
              value: `${recBase} Baseline + ${recAutoAdd} Autoscale → ${usd(opt1Cost, 2)}/mo (saves ${usd(opt1Sav, 2)}/mo · ${opt1RateNote})`,
            },
          ]
        : [
            {
              label: `Option ${p1Rank} · ${recBase} Baseline + Autoscale to ${recMax}`,
              badge: recOpt === 1 ? "Recommended" : "Selected",
              badgeTone: recOpt === 1 ? "emerald" : "sky",
              value: opt1Plan,
            },
            {
              label: "Projected net savings",
              value: `${usd(opt1Sav, 2)} / month (${opt1Pct}% efficiency gain · ${opt1RateNote})`,
            },
            {
              label: recOpt === 2 ? "Recommended · Option 1" : `Alternative · Option ${p2Rank} (0-Baseline)`,
              badge: recOpt === 2 ? "Saves more" : undefined,
              badgeTone: "emerald",
              muted: recOpt !== 2,
              value: `0-Baseline Pure Autoscale (0 → ${recMax}) → ${usd(opt2Cost, 2)}/mo (saves ${usd(opt2Sav, 2)}/mo · no lock-in)`,
            },
          ];

    return { before: beforeW01.length ? beforeW01 : ev.current_state, after: afterW01 };
  }, [wCap, propSized, w03, ev, pr, opt, recOpt, p1Rank, p2Rank, c.target_table, data.w01]);

  const approve = () =>
    decide.mutate({
      change_set_id: c.change_set_id,
      action: "approve",
      ...(wCap || hasOpt2 ? { selected_option: opt } : {}),
    });
  const reject = () =>
    decide.mutate(
      { change_set_id: c.change_set_id, action: "reject", reason, note },
      { onSuccess: () => { setRejectOpen(false); onClose(); } },
    );

  return (
    <motion.aside
      key={c.change_set_id}
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 24 }}
      transition={{ duration: 0.22 }}
      className="panel flex max-h-[calc(100vh-6rem)] flex-col overflow-hidden"
    >
      {/* header */}
      <div className="border-b border-zinc-200 p-5 dark:border-ink-800">
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge className={cn(meta.color, meta.ring)}>{meta.label}</Badge>
            {(c.rule_ids || []).map((r) => <Badge key={r} className="font-mono">{r}</Badge>)}
            <RegionBadge c={c} info={data.regions} withName />
            <Badge>{titleize(c.state || "")}</Badge>
            {propSized && (
              <Badge className="bg-emerald-500/10 text-emerald-600 ring-emerald-500/30 dark:text-emerald-300">
                <CheckCircle2 className="h-3 w-3" /> Properly sized
              </Badge>
            )}
            {two && <Badge className="text-amber-300 ring-amber-500/30">2-person</Badge>}
            {c.governance_scope === "FINOPS" && (
              <Badge className="bg-amber-500/10 text-amber-300 ring-amber-500/30" title="Only FinOps / governance approvers can approve this">
                <Landmark className="h-3 w-3" />FinOps
              </Badge>
            )}
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-white/5" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="mt-3 flex items-end justify-between gap-4">
          <div className="min-w-0">
            <div className="truncate text-lg font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">{targetLabel(c)}</div>
            <div className="mt-0.5 flex items-center gap-1 font-mono text-[11px] text-zinc-500">
              <span className="truncate">{fullPath(c)}</span>
              <CopyButton text={fullPath(c)} label="Path copied" />
              <span className="text-zinc-400">·</span>
              <span>{c.change_set_id.slice(0, 8)}</span>
              <CopyButton text={c.change_set_id} label="ID copied" />
            </div>
          </div>
          <div className="shrink-0 text-right">
            <div className="gradient-text font-mono text-3xl font-bold">
              <AnimatedNumber value={headline} format={(n) => usd(n)} />
            </div>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500">
              {propSized ? "no change needed · optimal" : `net / month${wCap ? ` · option ${activeRank}` : ""}`}
            </div>
          </div>
        </div>
      </div>

      {/* body */}
      <div className="flex-1 space-y-6 overflow-y-auto p-5">
        {(wCap || hasOpt2) && (
          <div className="space-y-2">
            {!propSized && (
              <div className="grid grid-cols-2 gap-1 rounded-lg bg-zinc-100 p-1 dark:bg-[#0c0c0f]">
                {orderedPlans.map((planId, idx) => {
                  const displayRank = idx + 1;
                  return (
                    <button
                      key={planId}
                      onClick={() => setOpt(planId)}
                      className={cn(
                        "rounded-md px-3 py-2 text-left text-xs font-medium transition-all",
                        opt === planId ? "bg-white text-sky-600 shadow-sm dark:bg-zinc-800 dark:text-sky-300" : "text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200",
                      )}
                    >
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span>{planTitleFor(planId, displayRank)}</span>
                        {wCap && planId === recOpt && (
                          <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-emerald-600 dark:text-emerald-300">
                            Recommended
                          </span>
                        )}
                      </div>
                      {wCap && (
                        <div className="mt-0.5 font-mono text-[11px] text-emerald-500">
                          est. {usd(planId === 2 ? ev.option_2_savings_usd : ev.option_1_savings_usd)}/mo efficiency
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
            {estimatorUrl && (
              <a
                href={estimatorUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-md border border-sky-500/30 bg-sky-500/5 px-2.5 py-1 text-[11px] font-medium text-sky-600 hover:bg-sky-500/10 dark:text-sky-300"
              >
                <ExternalLink className="h-3 w-3" />
                Verify slot simulation &amp; CUD commitments in GCP Slot Estimator
              </a>
            )}
          </div>
        )}

        {wCap && (
          <Section title="Slot usage vs. baseline & autoscale limit" icon={<BarChart3 className="h-3.5 w-3.5" />}>
            <SlotSizingChart
              sizing={ev.capacity_sizing as CapacitySizing | undefined}
              recBaseline={num(pr.recommended_baseline_slots ?? (w01 ? data.w01?.baseline : data.w03?.rec_baseline))}
              recMax={num(pr.recommended_autoscale_max_slots ?? (w01 ? data.w01?.max : data.w03?.rec_max))}
              curBaseline={w03 ? num(ev.current_baseline_slots ?? pr.current_baseline_slots) : undefined}
              curMax={
                w03
                  ? num(
                      ev.current_max_slots ??
                        num(ev.current_baseline_slots ?? pr.current_baseline_slots) +
                          num(ev.current_autoscale_add_slots ?? pr.current_autoscale_max_slots),
                    )
                  : undefined
              }
              committedSlots={
                w03 && (ev.covered_baseline_slots != null || ev.committed_slots_edition != null)
                  ? num(ev.covered_baseline_slots ?? ev.committed_slots_edition)
                  : undefined
              }
              isReservation={w03}
              selectedOption={opt}
              recommendedOption={recOpt}
              option2Mode={String(ev.option_2_mode || pr.option_2_mode || "PURE_AUTOSCALE")}
              bytesTib30d={ev.bytes_scanned_tib_30d != null ? num(ev.bytes_scanned_tib_30d) : null}
              windowDays={num(ev.sizing_window_days || 30)}
            />
          </Section>
        )}

        {bullets.length > 0 && (
          <Section title="Why this matters">
            <ul className="space-y-1.5">
              {bullets.map((b, i) => (
                <li key={i} className="flex gap-2 text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
                  <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" />
                  <span><Rich text={b} /></span>
                </li>
              ))}
            </ul>
          </Section>
        )}

        <Section title="How this saving is calculated" icon={<Calculator className="h-3.5 w-3.5" />}>
          <SavingsMathPanel
            sm={ev.savings_math as SavingsMath | undefined}
            net={num(c.net_monthly_value_usd)}
            gross={num(c.gross_monthly_savings_usd)}
            recurring={num((c as any).recurring_monthly_cost_usd)}
          />
        </Section>

        {(ev.current_state || ev.proposed_state) && (
          <Section title="Before vs. after">
            <StateCompare before={comparePanels.before} after={comparePanels.after} />
          </Section>
        )}

        {(c.risk_notes || []).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {(c.risk_notes || []).map((r) =>
              r === "INFORMATIONAL_PROPERLY_SIZED" ? (
                <Badge key={r} className="bg-emerald-500/10 text-[10px] text-emerald-500 ring-emerald-500/25 dark:text-emerald-300">
                  <CheckCircle2 className="h-3 w-3" />Properly sized · informational scorecard
                </Badge>
              ) : (
                <Badge key={r} className="text-[10px] text-amber-400 ring-amber-500/20">
                  <AlertTriangle className="h-3 w-3" />{titleize(r.toLowerCase())}
                </Badge>
              ),
            )}
          </div>
        )}

        {(c.director_name || c.department) && (
          <div className="grid grid-cols-3 gap-2 rounded-lg border border-zinc-200 p-3 text-xs dark:border-ink-800">
            <div>
              <div className="label !text-[10px]">Director</div>
              <div className="mt-0.5 font-medium" title={c.attribution_source ? `Attributed via ${c.attribution_source}` : undefined}>
                {c.director_name || "—"}
              </div>
              {c.attribution_source && (
                <div className="mt-0.5 text-[10px] text-zinc-500">via {titleize(String(c.attribution_source).toLowerCase())}</div>
              )}
            </div>
            <div><div className="label !text-[10px]">Department</div><div className="mt-0.5 font-medium">{c.department || "—"}</div></div>
            <div>
              <div className="label !text-[10px]">Team usage</div>
              <div className="mt-0.5 font-mono">{c.team_readers_count ?? 0} readers · {c.team_queries_count ?? 0} q</div>
            </div>
          </div>
        )}


        {(() => {
          // Service-account spend rolls up to the human owner in the SA owner table.
          const sa = (ev.accountable_owner || ev.service_account_owner) as
            | { principal?: string; owner_email?: string; director_name?: string; application?: string; source?: string }
            | undefined;
          const unmapped = (c.risk_notes || []).some((r) => r.includes("UNMAPPED_SERVICE_ACCOUNT"));
          if (!sa && !unmapped) return null;
          return (
            <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-ink-800">
              <div className="label !text-[10px]">Accountable owner</div>
              {sa && sa.source !== "PRINCIPAL" ? (
                <div className="mt-0.5">
                  <span className="font-mono">{sa.principal}</span>
                  {sa.application ? <span className="text-zinc-500"> ({sa.application})</span> : null} → owned by{" "}
                  <span className="font-medium">{sa.owner_email}</span>
                  {sa.director_name ? <span className="text-zinc-500"> · Director {sa.director_name}</span> : null}
                </div>
              ) : sa ? (
                <div className="mt-0.5">Run by <span className="font-medium">{sa.owner_email}</span></div>
              ) : (
                <div className="mt-0.5 text-amber-400">
                  Run by a service account with no owner in the service-account owner table: add it there so this spend
                  rolls up to a Director.
                </div>
              )}
            </div>
          );
        })()}

        {ev.current_sql && ev.proposed_sql && (
          <Section title="SQL rewrite">
            <CodeBlock code={String(ev.current_sql)} title="Current SQL" tone="rose" />
            <CodeBlock code={String(ev.proposed_sql)} title="Proposed SQL" tone="emerald" />
          </Section>
        )}

        {ddl && !(ev.current_sql && ev.proposed_sql && isPr) && (
          <Section title={propSized ? "Sizing verification note" : "Generated change"}>
            <CodeBlock
              code={String(ddl)}
              title={propSized ? "Status · no DDL needed" : hasOpt2 ? `DDL · option ${opt}` : "DDL"}
              tone={propSized ? "emerald" : "sky"}
            />
          </Section>
        )}

        <Section title="Confidence" icon={<ShieldCheck className="h-3.5 w-3.5" />}>
          <div className="flex items-center gap-3">
            <Meter value={num(c.confidence) * 100} className="flex-1" />
            <span className="font-mono text-sm">{Math.round(num(c.confidence) * 100)}%</span>
          </div>
          <div className="grid grid-cols-5 gap-2">
            {["base", "d_summation", "d_volatility", "d_history", "d_window"].map((k) => (
              <div key={k} className="rounded-md bg-zinc-100 p-2 text-center dark:bg-white/[0.03]">
                <div className="font-mono text-sm">{c.factors?.[k] != null ? num(c.factors[k]).toFixed(2) : "—"}</div>
                <div className="text-[9px] uppercase tracking-wider text-zinc-500">{k.replace("d_", "")}</div>
              </div>
            ))}
          </div>
        </Section>

        {approvals.length > 0 && (
          <Section title="Sign-offs" icon={<Users className="h-3.5 w-3.5" />}>
            {approvals.map((a, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                <span className="font-medium">{a.principal}</span>
                <Badge>{titleize(a.role || "")}</Badge>
                <span className="ml-auto text-zinc-500">{fmtDate(a.at, true)}</span>
              </div>
            ))}
          </Section>
        )}

        {(c.state_history || []).length > 0 && (
          <Section title="History" icon={<History className="h-3.5 w-3.5" />}>
            <ol className="relative ml-2 space-y-3 border-l border-zinc-200 pl-4 dark:border-ink-800">
              {(c.state_history || []).map((h, i) => (
                <li key={i} className="text-xs">
                  <span className="absolute -left-[5px] mt-1 h-2.5 w-2.5 rounded-full bg-sky-400 ring-4 ring-white dark:ring-ink-900" />
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{titleize(h.state || "")}</span>
                    <span className="text-zinc-500">{h.actor}</span>
                    <span className="ml-auto text-zinc-500">{fmtDate(h.at, true)}</span>
                  </div>
                  {h.note && <div className="mt-0.5 text-zinc-500">{h.note}</div>}
                </li>
              ))}
            </ol>
          </Section>
        )}

        <details className="group rounded-lg border border-zinc-200 dark:border-ink-800">
          <summary className="cursor-pointer select-none px-3 py-2 text-xs font-medium text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200">
            Raw telemetry & proposal JSON
          </summary>
          <pre className="max-h-72 overflow-auto border-t border-zinc-200 bg-zinc-950 p-3 font-mono text-[11px] text-zinc-400 dark:border-ink-800">
            {JSON.stringify({ evidence: ev, proposed: pr, factors: c.factors }, null, 2)}
          </pre>
        </details>
      </div>

      {/* actions */}
      <div className="flex flex-wrap items-center gap-2 border-t border-zinc-200 bg-zinc-50/80 p-4 dark:border-ink-800 dark:bg-black/20">
        {propSized ? (
          <span className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs font-medium text-emerald-600 dark:text-emerald-300">
            <CheckCircle2 className="h-4 w-4" />
            Properly sized — no capacity change needed
          </span>
        ) : (
          <Button variant="success" onClick={approve} loading={decide.isPending && decide.variables?.action === "approve"}>
            <CheckCircle2 className="h-4 w-4" />{approveLabel}
          </Button>
        )}
        {isPr && (
          <Button variant="secondary" onClick={() => onPr(c)}>
            <GitPullRequest className="h-4 w-4" />Open GitHub PR
          </Button>
        )}
        <Button variant="ghost" className="ml-auto text-rose-400" onClick={() => setRejectOpen(true)}>
          <XCircle className="h-4 w-4" />{propSized ? "Dismiss" : "Reject"}
        </Button>
      </div>

      <Dialog
        open={rejectOpen}
        onOpenChange={setRejectOpen}
        title="Reject recommendation"
        description={`The card will be snoozed for ${data.reason_snooze_days?.[reason] ?? 30} days based on the reason you pick.`}
      >
        <div className="space-y-3">
          <label className="flex flex-col gap-1">
            <span className="label">Reason</span>
            <select className="input" value={reason} onChange={(e) => setReason(e.target.value)}>
              {data.reasons.filter((r) => !r.startsWith("REGRESSION_")).map((r) => (
                <option key={r} value={r}>{titleize(r.toLowerCase())}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="label">Note (optional)</span>
            <textarea className="input min-h-[80px]" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Context for the owner…" />
          </label>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setRejectOpen(false)}>Cancel</Button>
            <Button variant="danger" onClick={reject} loading={decide.isPending && decide.variables?.action === "reject"}>
              Reject & snooze
            </Button>
          </div>
        </div>
      </Dialog>
    </motion.aside>
  );
}
