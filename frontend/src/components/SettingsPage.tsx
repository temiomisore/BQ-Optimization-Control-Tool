import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle, ArrowLeft, CheckCircle2, ChevronDown, Download, History, Info, Lock, MinusCircle, Pencil, Plus,
  RefreshCw, RotateCcw, Search, ShieldCheck, Undo2, UserCog, Users, X, XCircle,
} from "lucide-react";
import {
  fetchSettings, fetchSettingsHistory, fetchSetupCheck, revertSettingChange, saveSetting, updateSettingsEditors,
  type CheckStatus, type SettingChange, type SettingItem, type SettingsAccess, type SetupCheck,
} from "@/lib/api";
import { useReviewer } from "@/lib/store";
import { ago, cn } from "@/lib/utils";
import { Badge, Button, Skeleton } from "./ui";
import { CodeBlock, CopyButton } from "./Code";

/* ----------------------------------------------------------------------- setup check */

const STATUS: Record<CheckStatus, { icon: React.ReactNode; tone: string; label: string }> = {
  ok: { icon: <CheckCircle2 className="h-4 w-4" />, tone: "text-emerald-500 dark:text-emerald-400", label: "Works" },
  warn: { icon: <AlertTriangle className="h-4 w-4" />, tone: "text-amber-500 dark:text-amber-400", label: "Warning" },
  fail: { icon: <XCircle className="h-4 w-4" />, tone: "text-rose-500 dark:text-rose-400", label: "Needs action" },
  info: { icon: <Info className="h-4 w-4" />, tone: "text-sky-500 dark:text-sky-400", label: "Optional" },
  skip: { icon: <MinusCircle className="h-4 w-4" />, tone: "text-zinc-400", label: "Skipped" },
};

function CheckRow({ x }: { x: SetupCheck }) {
  const needsFix = !!x.fix && x.status !== "ok";
  const [open, setOpen] = useState(x.status === "fail" || x.status === "warn");
  const s = STATUS[x.status] || STATUS.skip;
  return (
    <div className="border-t border-zinc-200 py-3 first:border-t-0 dark:border-ink-800">
      <div className="flex items-start gap-3">
        <span className={cn("mt-0.5 shrink-0", s.tone)} title={s.label}>{s.icon}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-zinc-800 dark:text-zinc-100">
            {x.title}
            {x.optional && <Badge className="text-[10px] text-zinc-500">optional</Badge>}
          </div>
          <div className="mt-0.5 break-words text-xs leading-relaxed text-zinc-500">{x.detail}</div>
          {x.why && <div className="mt-0.5 text-[11px] text-zinc-400">Why it matters: {x.why}</div>}
          {needsFix && (
            <button
              type="button"
              onClick={() => setOpen(!open)}
              className="mt-1.5 flex items-center gap-1 text-xs font-medium text-sky-500 hover:text-sky-400"
            >
              <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
              How to fix
            </button>
          )}
          {needsFix && open && (
            <div className="mt-2">
              <CodeBlock code={String(x.fix)} title="Run this (as an admin)" tone="sky" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SetupCheckPanel() {
  const [run, setRun] = useState(0);
  const q = useQuery({
    queryKey: ["setup-check", run],
    queryFn: () => fetchSetupCheck(run > 0),
    staleTime: 5 * 60 * 1000,
  });
  const rep = q.data;
  const groups = useMemo(() => {
    if (!rep) return [] as { title: string; rows: SetupCheck[] }[];
    const out = [{ title: "General", rows: rep.checks.filter((c) => !c.region) }];
    for (const r of rep.route) {
      out.push({ title: r === rep.home ? `${r} · home region` : r, rows: rep.checks.filter((c) => c.region === r) });
    }
    return out.filter((g) => g.rows.length);
  }, [rep]);

  return (
    <section className="panel space-y-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <ShieldCheck className="h-5 w-5 text-sky-400" /> Setup check
          </h2>
          <p className="mt-0.5 max-w-3xl text-xs text-zinc-500">
            Can the tool collect, analyze and write with the permissions it has? Each check is read-only (it reads at
            most one row) and anything that fails shows the exact command to fix it.
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => setRun((n) => n + 1)} loading={q.isFetching}>
          <RefreshCw className="h-3.5 w-3.5" /> Run again
        </Button>
      </div>

      {q.isLoading && <Skeleton className="h-40 w-full" />}
      {q.error && <div className="text-sm text-rose-400">Couldn't run the checks: {(q.error as Error).message}</div>}

      {rep && (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {rep.summary.ready ? (
              <Badge className="bg-emerald-500/10 text-emerald-600 ring-emerald-500/30 dark:text-emerald-300">
                <CheckCircle2 className="h-3 w-3" /> Ready to run
              </Badge>
            ) : (
              <Badge className="bg-rose-500/10 text-rose-600 ring-rose-500/30 dark:text-rose-300">
                <XCircle className="h-3 w-3" /> {rep.summary.blocking} to fix before it can run fully
              </Badge>
            )}
            <span className="text-emerald-500">✓ {rep.summary.ok} working</span>
            {rep.summary.warn > 0 && <span className="text-amber-500">⚠ {rep.summary.warn} warning{rep.summary.warn > 1 ? "s" : ""}</span>}
            {rep.summary.fail > 0 && <span className="text-rose-500">✕ {rep.summary.fail} need action</span>}
            {rep.summary.info > 0 && <span className="text-sky-500">ⓘ {rep.summary.info} optional</span>}
            <span className="text-zinc-400">
              · checked as <span className="font-mono">{rep.identity || "unknown"}</span> · {ago(rep.ran_at)} · {rep.duration_s}s
            </span>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            {groups.map((g) => (
              <div key={g.title} className="rounded-lg border border-zinc-200 px-4 py-1 dark:border-ink-800">
                <div className="label pt-3 !text-[10px]">{g.title}</div>
                {g.rows.map((x) => <CheckRow key={x.id} x={x} />)}
              </div>
            ))}
          </div>

          <div className="rounded-lg border border-sky-500/20 bg-sky-500/5 p-3 text-xs text-zinc-600 dark:text-zinc-300">
            These checks run as this web app's account. If your nightly job runs as a different account, run the same
            checks as that account:
            <div className="mt-1.5 flex items-center gap-1 font-mono text-[11px] text-sky-600 dark:text-sky-300">
              <span className="break-all">{rep.check_command}</span>
              <CopyButton text={rep.check_command || ""} label="Command copied" />
            </div>
          </div>
        </>
      )}
    </section>
  );
}

/* --------------------------------------------------------------- shared settings helpers */

const BUCKET_ICON: Record<string, string> = { startup: "🧱", everyday: "🎛️", safety: "🔒" };

function fmtValue(v: unknown): { text: string; empty: boolean } {
  if (v === null || v === undefined || v === "") return { text: "not set", empty: true };
  if (typeof v === "boolean") return { text: v ? "On" : "Off", empty: false };
  if (Array.isArray(v)) return v.length ? { text: v.join(", "), empty: false } : { text: "none", empty: true };
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    return entries.length ? { text: entries.map(([k, x]) => `${k}: ${x}`).join(" · "), empty: false } : { text: "none", empty: true };
  }
  return { text: String(v), empty: false };
}

/** Text the inline editor starts with, per kind. */
function toDraft(s: SettingItem): string {
  const v = s.value;
  if (v === null || v === undefined) return "";
  if (s.kind === "map" || s.kind === "weights") {
    return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}: ${x === null ? "null" : x}`).join("\n");
  }
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "boolean") return v ? "on" : "off";
  return String(v);
}

function useSettingsMutations() {
  const qc = useQueryClient();
  const { email } = useReviewer();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["settings"] });
    qc.invalidateQueries({ queryKey: ["settings-history"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const onError = (e: Error) => toast.error(e.message);
  const save = useMutation({
    mutationFn: (b: { key: string; value?: unknown; action?: "set" | "reset"; note?: string }) =>
      saveSetting({ ...b, principal: email }),
    onSuccess: (r) => {
      toast.success(r.action === "RESET" ? "Back to the config.yaml value" : "Saved", { description: `Takes effect: ${r.applies}` });
      if (r.warning) toast.warning(r.warning);
      refresh();
    },
    onError,
  });
  const revert = useMutation({
    mutationFn: (change_id: string) => revertSettingChange({ change_id, principal: email }),
    onSuccess: () => { toast.success("Change undone"); refresh(); },
    onError,
  });
  const editors = useMutation({
    mutationFn: (b: { action: "add" | "remove"; email: string }) => updateSettingsEditors({ ...b, principal: email }),
    onSuccess: (_r, b) => { toast.success(b.action === "add" ? `${b.email} can now edit settings` : `${b.email} removed`); refresh(); },
    onError,
  });
  return { save, revert, editors };
}

/* --------------------------------------------------------------------- who can edit */

function AccessPanel({ access }: { access: SettingsAccess }) {
  const { editors } = useSettingsMutations();
  const [newEmail, setNewEmail] = useState("");
  const role = access.is_admin ? "Settings admin" : access.is_editor ? "Settings editor" : "View only";
  return (
    <section className="panel space-y-3 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <Users className="h-5 w-5 text-sky-400" /> Who can edit settings
          </h2>
          <p className="mt-0.5 max-w-3xl text-xs text-zinc-500">
            Admins are set when the tool is deployed and add or remove editors here. Editors change everyday settings.
            Settings that change the savings numbers also need a FinOps approver; verification thresholds need an admin.
            Startup settings and safety switches stay deploy-time only.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-zinc-500">You:</span>
          <span className="font-mono">{access.email || "unknown"}</span>
          <Badge className={access.is_editor ? "bg-emerald-500/10 text-emerald-600 ring-emerald-500/30 dark:text-emerald-300" : "text-zinc-500"}>
            <UserCog className="h-3 w-3" /> {role}
          </Badge>
          {access.is_finops && <Badge className="bg-amber-500/10 text-amber-600 ring-amber-500/30 dark:text-amber-300">FinOps approver</Badge>}
        </div>
      </div>

      {access.demo_identity && (
        <div className="flex gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-200">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>
            Demo mode: identities aren't verified (anyone can pick who they are in the header), so these permissions are
            for show. In production, put the app behind Identity-Aware Proxy so every change is signed by a real login.
          </span>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-lg border border-zinc-200 p-3 dark:border-ink-800">
          <div className="label !text-[10px]">Settings admins · set at deploy time</div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {access.admins.length ? access.admins.map((a) => <Badge key={a} className="font-mono">{a}</Badge>)
              : <span className="text-xs italic text-zinc-400">none yet: add emails to governance.settings_admins in config.yaml</span>}
          </div>
        </div>
        <div className="rounded-lg border border-zinc-200 p-3 dark:border-ink-800">
          <div className="label !text-[10px]">Settings editors · added here by admins</div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {access.editors.length ? access.editors.map((e) => (
              <Badge key={e} className="font-mono">
                {e}
                {access.is_admin && (
                  <button
                    type="button"
                    title={`Remove ${e}`}
                    className="ml-0.5 rounded hover:text-rose-400"
                    onClick={() => editors.mutate({ action: "remove", email: e })}
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </Badge>
            )) : <span className="text-xs italic text-zinc-400">no editors yet</span>}
          </div>
          {access.is_admin && (
            <form
              className="mt-3 flex gap-2"
              onSubmit={(ev) => {
                ev.preventDefault();
                if (newEmail.trim()) editors.mutate({ action: "add", email: newEmail.trim() }, { onSuccess: () => setNewEmail("") });
              }}
            >
              <input className="input py-1.5 text-xs" placeholder="name@company.com" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} />
              <Button size="sm" variant="secondary" type="submit" loading={editors.isPending} className="shrink-0 whitespace-nowrap">
                <Plus className="h-3.5 w-3.5" /> Add editor
              </Button>
            </form>
          )}
        </div>
      </div>
    </section>
  );
}

/* --------------------------------------------------------------------- one setting */

function SourceBadge({ source }: { source: string }) {
  const env = source.includes("env ");
  const yaml = source.startsWith("config.yaml");
  const app = source === "saved in the app";
  return (
    <Badge
      className={cn(
        "max-w-full font-mono text-[10px]",
        env ? "bg-violet-500/10 text-violet-600 ring-violet-500/30 dark:text-violet-300"
          : app ? "bg-emerald-500/10 text-emerald-600 ring-emerald-500/30 dark:text-emerald-300"
            : yaml ? "bg-sky-500/10 text-sky-600 ring-sky-500/30 dark:text-sky-300"
              : "text-zinc-500",
      )}
      title={env ? `Set by an environment variable on the deployment (${source})`
        : app ? "Saved on this page" : yaml ? "Set in config.yaml" : "Built-in default"}
    >
      <span className="truncate">{source}</span>
    </Badge>
  );
}

const EDIT_TAG: Record<string, string> = { finops: "FinOps only", admin: "Admin only" };

function Editor({ s, onDone }: { s: SettingItem; onDone: () => void }) {
  const { save } = useSettingsMutations();
  const [draft, setDraft] = useState(toDraft(s));
  const [mode, setMode] = useState(s.kind === "regions_mode" && s.value === "auto" ? "auto" : "list");
  const [note, setNote] = useState("");
  const submit = () => {
    const value = s.kind === "regions_mode" && mode === "auto" ? "auto" : draft;
    save.mutate({ key: s.key, value, note: note || undefined }, { onSuccess: onDone });
  };
  let field: React.ReactNode;
  if (s.kind === "bool") {
    field = (
      <select className="input py-1.5 text-xs" value={draft} onChange={(e) => setDraft(e.target.value)}>
        <option value="on">On</option>
        <option value="off">Off</option>
      </select>
    );
  } else if (s.kind === "enum") {
    field = (
      <select className="input py-1.5 text-xs" value={draft} onChange={(e) => setDraft(e.target.value)}>
        {(s.choices || []).map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    );
  } else if (s.kind === "map" || s.kind === "weights") {
    field = (
      <textarea className="input min-h-[84px] py-1.5 font-mono text-xs" value={draft} onChange={(e) => setDraft(e.target.value)}
        placeholder={s.kind === "weights" ? "1: 1.0\n2: 1.3\n3: 2.0\n4: 1.5" : "user_email: email"} />
    );
  } else if (s.kind === "int" || s.kind === "float") {
    field = (
      <input className="input py-1.5 font-mono text-xs" type="number" value={draft} step={s.kind === "int" ? 1 : "any"}
        min={s.min ?? undefined} max={s.max ?? undefined} onChange={(e) => setDraft(e.target.value)}
        placeholder={s.nullable ? "empty = not set" : ""} />
    );
  } else {
    field = (
      <input className="input py-1.5 font-mono text-xs" value={draft} onChange={(e) => setDraft(e.target.value)}
        placeholder={s.kind === "region_list" || s.kind === "regions_mode" ? "us, us-east4" : s.nullable ? "empty = not set" : ""} />
    );
  }
  return (
    <div className="mt-2 space-y-2 rounded-lg border border-sky-500/30 bg-sky-500/5 p-3">
      {s.kind === "regions_mode" && (
        <div className="flex gap-3 text-xs">
          <label className="flex items-center gap-1"><input type="radio" checked={mode === "auto"} onChange={() => setMode("auto")} /> Find automatically</label>
          <label className="flex items-center gap-1"><input type="radio" checked={mode === "list"} onChange={() => setMode("list")} /> Fixed list</label>
        </div>
      )}
      {!(s.kind === "regions_mode" && mode === "auto") && field}
      <input className="input py-1.5 text-xs" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why are you changing this? (optional, shown in history)" />
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" onClick={submit} loading={save.isPending}>Save</Button>
        <Button size="sm" variant="ghost" onClick={onDone}>Cancel</Button>
        <span className="text-[11px] text-zinc-500">Takes effect: {s.applies_text}</span>
      </div>
    </div>
  );
}

function SettingRow({ s }: { s: SettingItem }) {
  const v = fmtValue(s.value);
  const [editing, setEditing] = useState(false);
  const { save } = useSettingsMutations();
  return (
    <div className="border-t border-zinc-200 py-2.5 first:border-t-0 dark:border-ink-800">
      <div className="grid gap-x-4 gap-y-1 md:grid-cols-[minmax(180px,1.1fr)_minmax(160px,1fr)_11rem_minmax(220px,1.6fr)] md:items-start">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-zinc-800 dark:text-zinc-100">
            {s.label}
            {EDIT_TAG[s.edit] && <Badge className="text-[10px] text-amber-600 ring-amber-500/30 dark:text-amber-300">{EDIT_TAG[s.edit]}</Badge>}
          </div>
          <div className="truncate font-mono text-[11px] text-zinc-500" title={s.key}>{s.key}</div>
        </div>
        <div className="flex min-w-0 items-start gap-1.5">
          <span className={cn("min-w-0 break-words font-mono text-[12px]", v.empty ? "italic text-zinc-400" : "text-zinc-800 dark:text-zinc-200")} title={v.text}>
            {v.text}
          </span>
          {s.editable_by_you && !editing && (
            <button type="button" title="Edit" onClick={() => setEditing(true)} className="shrink-0 rounded p-0.5 text-zinc-400 hover:bg-zinc-100 hover:text-sky-500 dark:hover:bg-white/5">
              <Pencil className="h-3.5 w-3.5" />
            </button>
          )}
          {!s.editable_by_you && s.locked_reason && (
            <span title={s.locked_reason} className="shrink-0 pt-0.5 text-zinc-400"><Lock className="h-3 w-3" /></span>
          )}
        </div>
        <div><SourceBadge source={s.source} /></div>
        <div className="text-xs leading-relaxed text-zinc-500">
          {s.desc}
          {s.env && !s.source.includes("env ") && <span className="text-zinc-400"> Env var: <span className="font-mono">{s.env}</span>.</span>}
          {s.saved && (
            <div className="mt-1 text-[11px] text-emerald-600 dark:text-emerald-400">
              Changed here by {s.saved.by || "someone"} {ago(s.saved.at)}{s.saved.note ? ` · “${s.saved.note}”` : ""}
              {s.editable_by_you && (
                <button type="button" className="ml-2 inline-flex items-center gap-0.5 text-sky-500 hover:text-sky-400"
                  onClick={() => save.mutate({ key: s.key, action: "reset" })}>
                  <RotateCcw className="h-3 w-3" /> Use config.yaml value
                </button>
              )}
            </div>
          )}
        </div>
      </div>
      {editing && <Editor s={s} onDone={() => setEditing(false)} />}
    </div>
  );
}

/* --------------------------------------------------------------------------- history */

function HistoryPanel({ labels, canUndo }: { labels: Record<string, SettingItem>; canUndo: (key: string) => boolean }) {
  const q = useQuery({ queryKey: ["settings-history"], queryFn: () => fetchSettingsHistory(50) });
  const { revert } = useSettingsMutations();
  const [open, setOpen] = useState(true);
  const rows = q.data?.changes || [];
  const label = (k: string) => (k === "_access.settings_editors" ? "Settings editors" : labels[k]?.label || k);
  const show = (c: SettingChange, v: unknown) => (c.action === "RESET" && v === c.value ? "config.yaml value" : fmtValue(v).text);
  return (
    <section className="panel space-y-3 p-5">
      <button type="button" className="flex w-full items-center justify-between" onClick={() => setOpen(!open)}>
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <History className="h-5 w-5 text-sky-400" /> Change history {rows.length ? <span className="text-xs font-normal text-zinc-500">({rows.length})</span> : null}
        </h2>
        <ChevronDown className={cn("h-4 w-4 text-zinc-500 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <>
          {q.isLoading && <Skeleton className="h-16 w-full" />}
          {!q.isLoading && !rows.length && <div className="text-xs text-zinc-500">No changes yet. Every save shows up here with who made it, when and why.</div>}
          {rows.length > 0 && (
            <div className="divide-y divide-zinc-200 dark:divide-ink-800">
              {rows.map((c) => (
                <div key={c.change_id} className="flex flex-wrap items-start gap-3 py-2.5 text-xs">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-zinc-800 dark:text-zinc-100">{label(c.key)}</div>
                    <div className="mt-0.5 font-mono text-[11px] text-zinc-500">
                      {fmtValue(c.previous_value).text} → {c.action === "RESET" ? "config.yaml value" : show(c, c.value)}
                    </div>
                    {c.note && <div className="mt-0.5 text-zinc-500">“{c.note}”</div>}
                  </div>
                  <div className="text-right text-zinc-500">
                    <div className="font-mono">{c.changed_by}</div>
                    <div>{ago(c.changed_at)}</div>
                  </div>
                  {canUndo(c.key) && (
                    <Button size="sm" variant="ghost" onClick={() => revert.mutate(c.change_id)} loading={revert.isPending}>
                      <Undo2 className="h-3.5 w-3.5" /> Undo
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}

/* --------------------------------------------------------------------------- settings */

function SettingsPanel() {
  const { email } = useReviewer();
  const q = useQuery({ queryKey: ["settings", email], queryFn: () => fetchSettings(email), staleTime: 30 * 1000 });
  const [filter, setFilter] = useState("");
  const data = q.data;
  const needle = filter.trim().toLowerCase();
  const shown = (data?.settings || []).filter(
    (s) => !needle || [s.label, s.key, s.desc, s.group, fmtValue(s.value).text].join(" ").toLowerCase().includes(needle),
  );
  const labels = useMemo(() => Object.fromEntries((data?.settings || []).map((s) => [s.key, s])), [data]);
  const canUndo = (key: string) => (key === "_access.settings_editors" ? !!data?.access?.is_admin : !!labels[key]?.editable_by_you);
  const editableCount = (data?.settings || []).filter((s) => s.editable_by_you).length;

  const download = () => {
    if (!data) return;
    const url = URL.createObjectURL(new Blob([data.yaml], { type: "text/yaml" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "config.yaml";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      {data?.access && <AccessPanel access={data.access} />}
      <section className="panel space-y-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">All settings{data ? ` (${data.settings.length})` : ""}</h2>
            <p className="mt-0.5 max-w-3xl text-xs text-zinc-500">
              Every setting the tool reads, what it's set to now, and where that value comes from.{" "}
              {editableCount > 0
                ? <>You can change <b>{editableCount}</b> of them: click the ✏️ next to a value. Changes are checked before saving and logged below.</>
                : <>You can't change any of them with your current identity.</>}{" "}
              Values pinned by an environment variable, startup settings and safety switches are changed at deploy time.
            </p>
          </div>
          {data && (
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" onClick={download}>
                <Download className="h-3.5 w-3.5" /> Download config.yaml
              </Button>
              <span className="flex items-center rounded-lg border border-zinc-200 pl-2 text-xs text-zinc-500 dark:border-ink-800">
                Copy
                <CopyButton text={data.yaml} label="config.yaml copied" />
              </span>
            </div>
          )}
        </div>

        <div className="relative max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
          <input className="input pl-9" placeholder="Find a setting…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>

        {q.isLoading && <Skeleton className="h-64 w-full" />}
        {q.error && <div className="text-sm text-rose-400">Couldn't load settings: {(q.error as Error).message}</div>}

        {data &&
          data.buckets.map((b) => {
            const rows = shown.filter((s) => s.bucket === b.id);
            if (!rows.length) return null;
            const groups = [...new Set(rows.map((s) => s.group))];
            return (
              <div key={b.id} className="rounded-lg border border-zinc-200 dark:border-ink-800">
                <div className="border-b border-zinc-200 px-4 py-3 dark:border-ink-800">
                  <div className="text-sm font-semibold">{BUCKET_ICON[b.id]} {b.title}</div>
                  <div className="text-xs text-zinc-500">{b.summary}</div>
                </div>
                <div className="px-4 pb-2">
                  {groups.map((g) => (
                    <div key={g}>
                      <div className="label pt-3 !text-[10px]">{g}</div>
                      {rows.filter((s) => s.group === g).map((s) => <SettingRow key={s.key} s={s} />)}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        {data && !data.config_file_found && (
          <div className="text-xs text-amber-500">No {data.config_file} found on the server: every value is a built-in default, an environment variable or saved here.</div>
        )}
      </section>
      {data && <HistoryPanel labels={labels} canUndo={canUndo} />}
    </>
  );
}

/* ------------------------------------------------------------------------------- page */

export function SettingsPage({ onBack }: { onBack: () => void }) {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" /> Back to recommendations
        </Button>
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Settings &amp; setup</h1>
          <p className="text-xs text-zinc-500">Check permissions, change everyday settings, and export a ready-to-use config.yaml.</p>
        </div>
      </div>
      <SetupCheckPanel />
      <SettingsPanel />
    </div>
  );
}
