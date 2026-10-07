import { useState } from "react";
import { motion } from "framer-motion";
import { AlertOctagon, ChevronDown, Lock, RotateCcw, ShieldAlert, Undo2 } from "lucide-react";
import type { ChangeSet, Dashboard, Receipt } from "@/lib/api";
import { useDecide } from "@/lib/store";
import { CLASS_META, cn, fmtDate, targetLabel, titleize, usd } from "@/lib/utils";
import { Badge, Button, Dialog, Empty } from "./ui";
import { CfoProofReceipt, SimulateDay14Banner, simFor, useDemoDay14 } from "./DemoProofReceipt";

const ROLLBACKABLE = ["APPLIED", "VERIFYING", "VERIFIED", "REGRESSED", "ROLLING_BACK"];

/* ---------------------------------------------------- rollback confirm */
function RollbackDialog({
  target,
  onClose,
}: {
  target: null | { id: string; label: string; w01: boolean; category?: string };
  onClose: () => void;
}) {
  const decide = useDecide();
  const [note, setNote] = useState("");
  return (
    <Dialog
      open={!!target}
      onOpenChange={(o) => !o && onClose()}
      title="Confirm rollback"
      description={
        target?.w01
          ? "This drops the reservation assignment and reservation, waits until queries route back to on-demand, then records the rollback. It can take up to ~3 minutes."
          : `Revert ${target?.label} to its pre-change state and snooze the recommendation.`
      }
    >
      <label className="flex flex-col gap-1">
        <span className="label">Note (optional)</span>
        <textarea className="input min-h-[70px]" value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <div className="flex justify-end gap-2 pt-4">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button
          variant="danger"
          loading={decide.isPending}
          onClick={() =>
            target &&
            decide.mutate(
              { change_set_id: target.id, action: "rollback", category: target.category || "MANUAL_REVERT", note },
              { onSettled: () => { setNote(""); onClose(); } },
            )
          }
        >
          <Undo2 className="h-4 w-4" />Roll back
        </Button>
      </div>
    </Dialog>
  );
}

const rowAnim = (i: number) => ({
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  transition: { delay: Math.min(i * 0.03, 0.3) },
});

/* ------------------------------------------------------ regression banner */
export function RegressedBanner({ cards }: { cards: ChangeSet[] }) {
  const [target, setTarget] = useState<null | { id: string; label: string; w01: boolean; category?: string }>(null);
  if (!cards.length) return null;
  return (
    <div className="space-y-2">
      {cards.map((c) => (
        <motion.div
          key={c.change_set_id}
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex flex-wrap items-center gap-3 rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 shadow-[0_0_30px_-8px_rgba(244,63,94,0.5)]"
        >
          <AlertOctagon className="h-5 w-5 shrink-0 animate-pulse text-rose-400" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-rose-200">
              Regression detected · {targetLabel(c)} <span className="font-mono text-xs text-rose-300/70">{(c.rule_ids || []).join(", ")}</span>
            </div>
            <div className="text-xs text-rose-200/80">{c.regression_message}</div>
          </div>
          <Button
            variant="danger"
            size="sm"
            onClick={() => setTarget({ id: c.change_set_id, label: targetLabel(c), w01: c.target_dataset === "PROJECT_WIDE_BILLING", category: "REGRESSION_PERFORMANCE" })}
          >
            <Undo2 className="h-3.5 w-3.5" />1-click rollback
          </Button>
        </motion.div>
      ))}
      <RollbackDialog target={target} onClose={() => setTarget(null)} />
    </div>
  );
}

/* ---------------------------------------------------------- blocked list */
export function BlockedList({ cards, data }: { cards: ChangeSet[]; data: Dashboard }) {
  const decide = useDecide();
  const [reason, setReason] = useState<Record<string, string>>({});
  if (!cards.length) return <Empty icon={<Lock className="h-5 w-5" />} title="Nothing blocked" hint="Guardrails haven't stopped any approved change." />;
  return (
    <div className="space-y-3">
      {cards.map((c, i) => (
        <motion.div key={c.change_set_id} {...rowAnim(i)} className="panel space-y-3 border-l-2 !border-l-amber-500 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge className={cn(CLASS_META[c.apply_class]?.color, CLASS_META[c.apply_class]?.ring)}>{CLASS_META[c.apply_class]?.short}</Badge>
            <span className="font-medium">{targetLabel(c)}</span>
            <span className="font-mono text-xs text-zinc-500">{(c.rule_ids || []).join(", ")}</span>
            <span className="ml-auto font-mono text-emerald-400">{usd(c.net_monthly_value_usd)}/mo</span>
          </div>
          <div className="flex gap-2 rounded-lg bg-amber-500/10 p-3 text-xs text-amber-200">
            <ShieldAlert className="h-4 w-4 shrink-0 text-amber-400" />
            <span>{c.blocker_message || "Blocked by a safety guardrail."}</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="input w-auto py-1.5 text-xs"
              value={reason[c.change_set_id] || data.reasons[0]}
              onChange={(e) => setReason({ ...reason, [c.change_set_id]: e.target.value })}
            >
              {data.reasons.filter((r) => !r.startsWith("REGRESSION_")).map((r) => (
                <option key={r} value={r}>{titleize(r.toLowerCase())}</option>
              ))}
            </select>
            <Button
              size="sm"
              variant="danger"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ change_set_id: c.change_set_id, action: "reject", reason: reason[c.change_set_id] || data.reasons[0] })}
            >
              Reject & snooze
            </Button>
            <Button size="sm" variant="secondary" disabled={decide.isPending} onClick={() => decide.mutate({ change_set_id: c.change_set_id, action: "reset" })}>
              <RotateCcw className="h-3.5 w-3.5" />Reset to review
            </Button>
          </div>
        </motion.div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------ regressed list tab */
export function RegressedList({ cards }: { cards: ChangeSet[] }) {
  if (!cards.length) return <Empty icon={<AlertOctagon className="h-5 w-5" />} title="No regressions" hint="Every applied change is performing within tolerance." />;
  return <RegressedBanner cards={cards} />;
}

/* --------------------------------------------------- rolled back list */
export function RolledBackList({ cards }: { cards: ChangeSet[] }) {
  const decide = useDecide();
  const [open, setOpen] = useState<string | null>(null);
  if (!cards.length) return <Empty icon={<Undo2 className="h-5 w-5" />} title="No rollbacks" hint="Nothing has been reverted yet." />;
  return (
    <div className="space-y-3">
      {cards.map((c, i) => {
        const isOpen = open === c.change_set_id;
        return (
          <motion.div key={c.change_set_id} {...rowAnim(i)} className="panel overflow-hidden">
            <button className="flex w-full items-center gap-3 p-4 text-left" onClick={() => setOpen(isOpen ? null : c.change_set_id)}>
              <Undo2 className="h-4 w-4 text-zinc-500" />
              <span className="font-medium">{targetLabel(c)}</span>
              <span className="font-mono text-xs text-zinc-500">{(c.rule_ids || []).join(", ")}</span>
              {c.snooze_until && <Badge>snoozed until {fmtDate(c.snooze_until)}</Badge>}
              <ChevronDown className={cn("ml-auto h-4 w-4 text-zinc-500 transition-transform", isOpen && "rotate-180")} />
            </button>
            {isOpen && (
              <div className="space-y-3 border-t border-zinc-200 p-4 text-sm dark:border-ink-800">
                <div className="grid gap-3 sm:grid-cols-3">
                  <div><div className="label">Rolled back by</div><div className="mt-1">{c.rollback_actor || "—"}</div></div>
                  <div><div className="label">Reason</div><div className="mt-1">{titleize((c.rejection_reason || "").toLowerCase()) || "—"}</div></div>
                  <div><div className="label">Forensics</div><div className="mt-1 break-all font-mono text-xs">{c.forensics_table || "—"}</div></div>
                </div>
                <div className="rounded-lg bg-zinc-100 p-3 text-xs text-zinc-600 dark:bg-white/[0.03] dark:text-zinc-400">{c.rollback_message || "No post-mortem notes."}</div>
                <Button size="sm" variant="secondary" disabled={decide.isPending} onClick={() => decide.mutate({ change_set_id: c.change_set_id, action: "unsnooze" })}>
                  <RotateCcw className="h-3.5 w-3.5" />Un-snooze & re-review
                </Button>
              </div>
            )}
          </motion.div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------- receipts table */
export function ReceiptsTable({ receipts }: { receipts: Receipt[] }) {
  const [target, setTarget] = useState<null | { id: string; label: string; w01: boolean }>(null);
  const [sim, setSim] = useDemoDay14();
  if (!receipts.length) return <Empty icon={<Undo2 className="h-5 w-5" />} title="No receipts yet" hint="Approved & applied changes show predicted vs. realized savings here." />;
  // Demo-only Day-14 preview for the seeded demo cards (see DemoProofReceipt.tsx).
  const simCount = receipts.filter((r) => simFor(r)).length;
  const showSim = sim && simCount > 0;
  return (
    <>
    {simCount > 0 && !sim && <SimulateDay14Banner count={simCount} onSimulate={() => setSim(true)} />}
    {showSim && <CfoProofReceipt receipts={receipts} onReset={() => setSim(false)} />}
    <div className="panel overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-zinc-200 text-left dark:border-ink-800">
            {["Target", "Rules", "Applied", "Predicted", "Realized", "Accuracy", "State", ""].map((h) => (
              <th key={h} className="label px-4 py-3">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {receipts.map((r) => {
            const label = r.target_dataset === "PROJECT_WIDE_BILLING" ? "Project-wide billing" : `${r.target_dataset}${r.target_table ? "." + r.target_table : ""}`;
            const s = showSim ? simFor(r) : null;
            const realized = s ? s.realized : r.realized_usd == null ? null : Number(r.realized_usd);
            const ratio = s ? (Number(r.predicted_usd) > 0 ? s.realized / Number(r.predicted_usd) : null) : r.realized_over_predicted;
            const simTag = s && <span className="ml-1 font-sans text-[10px] text-amber-500">sim</span>;
            return (
              <tr key={r.change_set_id} className="border-b border-zinc-100 transition-colors hover:bg-zinc-50 dark:border-ink-800/60 dark:hover:bg-white/[0.02]">
                <td className="px-4 py-3 font-medium">{label}</td>
                <td className="px-4 py-3 font-mono text-xs text-zinc-500">{(r.rule_ids || []).join(", ")}</td>
                <td className="px-4 py-3 text-xs text-zinc-500">{fmtDate(r.applied_at)}</td>
                <td className="px-4 py-3 font-mono">{usd(r.predicted_usd)}</td>
                <td className="px-4 py-3 font-mono text-emerald-400">{realized == null ? "—" : usd(realized)}{simTag}</td>
                <td className="px-4 py-3 font-mono text-xs">{ratio == null ? "—" : `${Math.round(Number(ratio) * 100)}%`}{simTag}</td>
                <td className="px-4 py-3">
                  <Badge className={s ? "text-emerald-400" : undefined}>{s ? "Verified" : titleize(r.state.toLowerCase())}</Badge>
                  {simTag}
                </td>
                <td className="px-4 py-3 text-right">
                  {ROLLBACKABLE.includes(r.state) && (
                    <Button size="sm" variant="ghost" className="text-rose-400" onClick={() => setTarget({ id: r.change_set_id, label, w01: r.target_dataset === "PROJECT_WIDE_BILLING" })}>
                      <Undo2 className="h-3.5 w-3.5" />Rollback
                    </Button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <RollbackDialog target={target} onClose={() => setTarget(null)} />
    </div>
    </>
  );
}
