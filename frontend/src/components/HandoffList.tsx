import { useState } from "react";
import { motion } from "framer-motion";
import { ChevronDown, GitBranch, GitMerge, GitPullRequest, FileCode2 } from "lucide-react";
import type { ChangeSet } from "@/lib/api";
import { useDecide } from "@/lib/store";
import { ago, cn, fmtDate, targetLabel, usd } from "@/lib/utils";
import { Badge, Button, Dialog, Empty } from "./ui";
import { CodeBlock, CopyButton } from "./Code";

/** Class 4 SQL rewrites handed off for code review (state PR_HANDED_OFF). */
export function HandoffList({ cards }: { cards: ChangeSet[] }) {
  const decide = useDecide();
  const [open, setOpen] = useState<string | null>(cards[0]?.change_set_id ?? null);
  const [merge, setMerge] = useState<ChangeSet | null>(null);
  const [prUrl, setPrUrl] = useState("");

  if (!cards.length) {
    return (
      <Empty
        icon={<GitPullRequest className="h-5 w-5" />}
        title="No PR hand-offs"
        hint="Approved Class 4 SQL rewrites land here after you run the execution script. Open the PR, then mark it merged to start savings verification."
      />
    );
  }

  const validUrl = !prUrl.trim() || /^https?:\/\//.test(prUrl.trim());

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-sky-500/20 bg-sky-500/5 p-3 text-xs text-zinc-600 dark:text-zinc-300">
        <b className="text-sky-600 dark:text-sky-300">How this works:</b> Class 4 rewrites change code, so the optimizer
        never applies them itself. For each card: create the branch, paste the proposed SQL into the file, open the PR
        (commands below), and once it's merged click <b>Mark PR merged</b> — the optimizer then measures real savings
        like any other change.
      </div>

      {cards.map((c, i) => {
        const h = c.handoff || {};
        const isOpen = open === c.change_set_id;
        return (
          <motion.div
            key={c.change_set_id}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(i * 0.03, 0.3) }}
            className="panel overflow-hidden border-l-2 !border-l-violet-500"
          >
            <button className="flex w-full flex-wrap items-center gap-3 p-4 text-left" onClick={() => setOpen(isOpen ? null : c.change_set_id)}>
              <GitPullRequest className="h-4 w-4 text-violet-400" />
              <span className="font-medium">{targetLabel(c)}</span>
              <span className="font-mono text-xs text-zinc-500">{(c.rule_ids || []).join(", ")}</span>
              <Badge className="text-violet-600 ring-violet-500/30 dark:text-violet-300">Awaiting PR merge</Badge>
              {h.handed_off_at && <span className="text-xs text-zinc-500">handed off {ago(h.handed_off_at)}</span>}
              <span className="ml-auto font-mono text-emerald-500 dark:text-emerald-400">{usd(c.net_monthly_value_usd)}/mo</span>
              <ChevronDown className={cn("h-4 w-4 text-zinc-500 transition-transform", isOpen && "rotate-180")} />
            </button>

            {isOpen && (
              <div className="space-y-4 border-t border-zinc-200 p-4 dark:border-ink-800">
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-lg border border-zinc-200 p-3 dark:border-ink-800">
                    <div className="label">Branch</div>
                    <div className="mt-1 flex items-center gap-1 break-all font-mono text-xs">
                      <GitBranch className="h-3.5 w-3.5 shrink-0 text-zinc-500" />{h.branch}<CopyButton text={h.branch || ""} />
                    </div>
                  </div>
                  <div className="rounded-lg border border-zinc-200 p-3 dark:border-ink-800">
                    <div className="label">File to change</div>
                    <div className="mt-1 flex items-center gap-1 break-all font-mono text-xs">
                      <FileCode2 className="h-3.5 w-3.5 shrink-0 text-zinc-500" />{h.file_path}<CopyButton text={h.file_path || ""} />
                    </div>
                  </div>
                </div>

                <ol className="space-y-3">
                  <li>
                    <div className="mb-1.5 text-xs font-semibold text-zinc-600 dark:text-zinc-300">1 · Create the branch & commit</div>
                    <CodeBlock code={h.git_commands || ""} title="git" tone="sky" />
                  </li>
                  <li>
                    <div className="mb-1.5 text-xs font-semibold text-zinc-600 dark:text-zinc-300">2 · Replace the query with the proposed SQL</div>
                    <div className="grid gap-3 xl:grid-cols-2">
                      {h.current_sql && <CodeBlock code={h.current_sql} title="Current SQL" tone="rose" />}
                      <CodeBlock code={h.proposed_sql || ""} title="Proposed SQL" tone="emerald" />
                    </div>
                  </li>
                  <li>
                    <div className="mb-1.5 text-xs font-semibold text-zinc-600 dark:text-zinc-300">3 · Open the pull request</div>
                    <CodeBlock code={h.gh_command || ""} title="GitHub CLI" tone="sky" />
                    {h.pr_body && (
                      <details className="mt-2 rounded-lg border border-zinc-200 dark:border-ink-800">
                        <summary className="cursor-pointer px-3 py-2 text-xs text-zinc-500">PR description (copy into GitHub)</summary>
                        <div className="relative border-t border-zinc-200 p-3 dark:border-ink-800">
                          <div className="absolute right-2 top-2"><CopyButton text={h.pr_body} label="PR description copied" /></div>
                          <pre className="whitespace-pre-wrap font-sans text-xs text-zinc-600 dark:text-zinc-400">{h.pr_body}</pre>
                        </div>
                      </details>
                    )}
                  </li>
                  <li>
                    <div className="mb-1.5 text-xs font-semibold text-zinc-600 dark:text-zinc-300">4 · After the PR merges</div>
                    <Button variant="success" onClick={() => { setPrUrl(""); setMerge(c); }}>
                      <GitMerge className="h-4 w-4" />Mark PR merged
                    </Button>
                  </li>
                </ol>
                <div className="text-[11px] text-zinc-500">Handed off {fmtDate(h.handed_off_at, true)} · change set {c.change_set_id}</div>
              </div>
            )}
          </motion.div>
        );
      })}

      <Dialog
        open={!!merge}
        onOpenChange={(o) => !o && setMerge(null)}
        title="Mark PR merged"
        description="Moves the card to Verifying. The optimizer compares the query's cost before vs. after over the verification window and records realized savings in Receipts."
      >
        <label className="flex flex-col gap-1">
          <span className="label">Pull request link (optional)</span>
          <input
            className={cn("input", !validUrl && "!border-rose-500")}
            placeholder="https://github.com/org/repo/pull/123"
            value={prUrl}
            onChange={(e) => setPrUrl(e.target.value)}
          />
          {!validUrl && <span className="text-xs text-rose-400">Link must start with http:// or https://</span>}
        </label>
        <div className="flex justify-end gap-2 pt-4">
          <Button variant="ghost" onClick={() => setMerge(null)}>Cancel</Button>
          <Button
            variant="success"
            disabled={!validUrl}
            loading={decide.isPending}
            onClick={() =>
              merge &&
              decide.mutate(
                { change_set_id: merge.change_set_id, action: "pr_merged", pr_url: prUrl.trim() || undefined },
                { onSuccess: () => setMerge(null) },
              )
            }
          >
            <GitMerge className="h-4 w-4" />Confirm merged
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
