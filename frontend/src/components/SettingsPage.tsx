import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowLeft, CheckCircle2, ChevronDown, Download, Info, MinusCircle, RefreshCw, Search, ShieldCheck,
  XCircle,
} from "lucide-react";
import { fetchSettings, fetchSetupCheck, type CheckStatus, type SettingItem, type SetupCheck } from "@/lib/api";
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

/* --------------------------------------------------------------------------- settings */

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

function SourceBadge({ source }: { source: string }) {
  const env = source.includes("env ");
  const yaml = source.startsWith("config.yaml");
  return (
    <Badge
      className={cn(
        "max-w-full font-mono text-[10px]",
        env ? "bg-violet-500/10 text-violet-600 ring-violet-500/30 dark:text-violet-300"
          : yaml ? "bg-sky-500/10 text-sky-600 ring-sky-500/30 dark:text-sky-300"
            : "text-zinc-500",
      )}
      title={env ? `Set by an environment variable on the deployment (${source})` : yaml ? "Set in config.yaml" : "Built-in default"}
    >
      <span className="truncate">{source}</span>
    </Badge>
  );
}

function SettingRow({ s }: { s: SettingItem }) {
  const v = fmtValue(s.value);
  return (
    <div className="grid gap-x-4 gap-y-1 border-t border-zinc-200 py-2.5 first:border-t-0 dark:border-ink-800 md:grid-cols-[minmax(180px,1.1fr)_minmax(160px,1fr)_11rem_minmax(220px,1.6fr)] md:items-start">
      <div className="min-w-0">
        <div className="text-sm font-medium text-zinc-800 dark:text-zinc-100">{s.label}</div>
        <div className="truncate font-mono text-[11px] text-zinc-500" title={s.key}>{s.key}</div>
      </div>
      <div
        className={cn("min-w-0 break-words font-mono text-[12px]", v.empty ? "italic text-zinc-400" : "text-zinc-800 dark:text-zinc-200")}
        title={v.text}
      >
        {v.text}
      </div>
      <div><SourceBadge source={s.source} /></div>
      <div className="text-xs leading-relaxed text-zinc-500">
        {s.desc}
        {s.env && !s.source.includes("env ") && <span className="text-zinc-400"> Env var: <span className="font-mono">{s.env}</span>.</span>}
      </div>
    </div>
  );
}

function SettingsPanel() {
  const q = useQuery({ queryKey: ["settings"], queryFn: fetchSettings, staleTime: 60 * 1000 });
  const [filter, setFilter] = useState("");
  const data = q.data;
  const needle = filter.trim().toLowerCase();
  const shown = (data?.settings || []).filter(
    (s) => !needle || [s.label, s.key, s.desc, s.group, fmtValue(s.value).text].join(" ").toLowerCase().includes(needle),
  );

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
    <section className="panel space-y-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">All settings{data ? ` (${data.settings.length})` : ""}</h2>
          <p className="mt-0.5 max-w-3xl text-xs text-zinc-500">
            Every setting the tool reads, what it's set to now, and where that value comes from. Read-only for now: to
            change a value, edit <span className="font-mono">config.yaml</span> (or set the environment variable shown)
            and redeploy.
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
        <div className="text-xs text-amber-500">No {data.config_file} found on the server: every value is a built-in default or an environment variable.</div>
      )}
    </section>
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
          <p className="text-xs text-zinc-500">Check permissions, see every setting in one place, and export a ready-to-use config.yaml.</p>
        </div>
      </div>
      <SetupCheckPanel />
      <SettingsPanel />
    </div>
  );
}
