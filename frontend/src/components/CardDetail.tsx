import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle, CheckCircle2, ChevronRight, GitPullRequest, History, ShieldCheck, Users, X, XCircle,
} from "lucide-react";
import type { ChangeSet, Dashboard } from "@/lib/api";
import { useDecide } from "@/lib/store";
import {
  CLASS_META, cn, ddlFor, fmtDate, fullPath, isW01, isW02, needsTwo, targetLabel, titleize, usd,
} from "@/lib/utils";
import { AnimatedNumber, Badge, Button, Dialog, Meter } from "./ui";
import { CodeBlock, CopyButton } from "./Code";
import { Rich, StateCompare } from "./StateCompare";

const num = (v: unknown) => Number(v || 0);

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
  const ev = c.evidence || {};
  const pr = c.proposed || {};
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
  const headline = w01
    ? num(opt === 2 ? ev.option_2_savings_usd : ev.option_1_savings_usd) || num(c.net_monthly_value_usd)
    : num(c.net_monthly_value_usd);
  const ddl = hasOpt2 && opt === 2 ? pr.generated_ddl_option2 : ddlFor(c);
  const isPr = c.apply_class === 4 || c.execution_route === "CI_PULL_REQUEST";
  const approvals = c.approvals || [];
  const two = needsTwo(c);
  const approveLabel = two
    ? approvals.length === 0
      ? "Approve (Sign-off 1/2: Data Owner)"
      : "Sign-off 2/2: Platform"
    : "Approve Change";
  const optLabels = w02
    ? ["Option 1: 50-Slot Human Sandbox + 50 GB Cap", "Option 2: Audit SQL"]
    : ["Option 1: Baseline + Autoscale", "Option 2: 0-Baseline Autoscale"];

  const bullets = useMemo(() => {
    if (w01) {
      const base = pr.recommended_baseline_slots ?? data.w01?.baseline;
      const max = pr.recommended_autoscale_max_slots ?? data.w01?.max;
      const src = ev.capacity_sizing?.source || ev.capacity_sizing?.method;
      return [
        `On-demand today: ${usd(ev.on_demand_spend_monthly)}/mo for ${num(ev.bytes_scanned_tib_30d).toLocaleString()} TiB scanned (30d).`,
        `Option 1 (1-yr commit): ${usd(ev.option_1_monthly_cost_usd)}/mo → saves ${usd(ev.option_1_savings_usd)}/mo.`,
        `Option 2 (0-baseline autoscale): ${usd(ev.option_2_monthly_cost_usd)}/mo → saves ${usd(ev.option_2_savings_usd)}/mo.`,
        `Sized from real slot telemetry: baseline ${base} · max ${max} slots${src ? ` (${src})` : ""}.`,
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
  }, [c, w01, w02, ev, pr, data.w01]);

  const approve = () =>
    decide.mutate({
      change_set_id: c.change_set_id,
      action: "approve",
      ...(w01 || hasOpt2 ? { selected_option: opt } : {}),
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
            <Badge>{titleize(c.state || "")}</Badge>
            {two && <Badge className="text-amber-300 ring-amber-500/30">2-person</Badge>}
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
              net / month{w01 ? ` · option ${opt}` : ""}
            </div>
          </div>
        </div>
      </div>

      {/* body */}
      <div className="flex-1 space-y-6 overflow-y-auto p-5">
        {(w01 || hasOpt2) && (
          <div className="grid grid-cols-2 gap-1 rounded-lg bg-zinc-100 p-1 dark:bg-[#0c0c0f]">
            {[1, 2].map((o) => (
              <button
                key={o}
                onClick={() => setOpt(o)}
                className={cn(
                  "rounded-md px-3 py-2 text-left text-xs font-medium transition-all",
                  opt === o ? "bg-white text-sky-600 shadow-sm dark:bg-zinc-800 dark:text-sky-300" : "text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200",
                )}
              >
                {optLabels[o - 1]}
                {w01 && (
                  <div className="mt-0.5 font-mono text-[11px] text-emerald-500">
                    saves {usd(o === 2 ? ev.option_2_savings_usd : ev.option_1_savings_usd)}/mo
                  </div>
                )}
              </button>
            ))}
          </div>
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

        {(ev.current_state || ev.proposed_state) && (
          <Section title="Before vs. after">
            <StateCompare before={ev.current_state} after={ev.proposed_state} />
          </Section>
        )}

        {(c.risk_notes || []).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {(c.risk_notes || []).map((r) => (
              <Badge key={r} className="text-[10px] text-amber-400 ring-amber-500/20">
                <AlertTriangle className="h-3 w-3" />{titleize(r.toLowerCase())}
              </Badge>
            ))}
          </div>
        )}

        {(c.director_name || c.department) && (
          <div className="grid grid-cols-3 gap-2 rounded-lg border border-zinc-200 p-3 text-xs dark:border-ink-800">
            <div><div className="label !text-[10px]">Owner</div><div className="mt-0.5 font-medium">{c.director_name || "—"}</div></div>
            <div><div className="label !text-[10px]">Department</div><div className="mt-0.5 font-medium">{c.department || "—"}</div></div>
            <div>
              <div className="label !text-[10px]">Team usage</div>
              <div className="mt-0.5 font-mono">{c.team_readers_count ?? 0} readers · {c.team_queries_count ?? 0} q</div>
            </div>
          </div>
        )}


        {ev.current_sql && ev.proposed_sql && (
          <Section title="SQL rewrite">
            <CodeBlock code={String(ev.current_sql)} title="Current SQL" tone="rose" />
            <CodeBlock code={String(ev.proposed_sql)} title="Proposed SQL" tone="emerald" />
          </Section>
        )}

        {ddl && !(ev.current_sql && ev.proposed_sql && isPr) && (
          <Section title="Generated change">
            <CodeBlock code={String(ddl)} title={hasOpt2 ? `DDL · option ${opt}` : "DDL"} tone="sky" />
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
        <Button variant="success" onClick={approve} loading={decide.isPending && decide.variables?.action === "approve"}>
          <CheckCircle2 className="h-4 w-4" />{approveLabel}
        </Button>
        {isPr && (
          <Button variant="secondary" onClick={() => onPr(c)}>
            <GitPullRequest className="h-4 w-4" />Open GitHub PR
          </Button>
        )}
        <Button variant="ghost" className="ml-auto text-rose-400" onClick={() => setRejectOpen(true)}>
          <XCircle className="h-4 w-4" />Reject
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
