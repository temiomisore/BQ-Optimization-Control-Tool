import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, ChevronDown, Code2, Download, ExternalLink, History, Info,
  ListFilter, Lock, MinusCircle, Pencil, Play, Plus, RefreshCw, RotateCcw, Search, ShieldCheck, Sparkles, Terminal,
  Undo2, UserCog, Users, X, XCircle,
} from "lucide-react";
import {
  fetchScanStatus, fetchSettings, fetchSettingsHistory, fetchSettingsOptions, fetchSetupCheck, fetchTableColumns,
  revertSettingChange, saveSetting, triggerScan, updateSettingsEditors,
  type CheckStatus, type LabeledOption, type RegionOption, type ScanStatus, type SettingChange, type SettingItem,
  type SettingsAccess, type SettingsOptions, type SetupCheck, type TableOption,
} from "@/lib/api";
import { useReviewer } from "@/lib/store";
import { ago, cn, regionName } from "@/lib/utils";
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
              <CodeBlock
                code={String(x.fix)}
                title={/^(gcloud|bq|python)\b/.test(String(x.fix).trim()) ? "Paste in Cloud Shell (as an admin)" : "How to fix"}
                tone="sky"
              />
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

const FALLBACK_REGIONS: RegionOption[] = [
  { value: "us", label: "US multi-region", group: "Multi-regions" },
  { value: "eu", label: "EU multi-region", group: "Multi-regions" },
  { value: "us-central1", label: "Iowa", group: "Americas" },
  { value: "us-east1", label: "South Carolina", group: "Americas" },
  { value: "us-east4", label: "Northern Virginia", group: "Americas" },
  { value: "us-west1", label: "Oregon", group: "Americas" },
  { value: "europe-west1", label: "Belgium", group: "Europe" },
  { value: "europe-west2", label: "London", group: "Europe" },
  { value: "asia-northeast1", label: "Tokyo", group: "Asia Pacific, Middle East & Africa" },
  { value: "australia-southeast1", label: "Sydney", group: "Asia Pacific, Middle East & Africa" },
];

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
    qc.invalidateQueries({ queryKey: ["settings-options"] });
    qc.invalidateQueries({ queryKey: ["table-columns"] });
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

function AccessPanel({ access, knownEmails }: { access: SettingsAccess; knownEmails?: LabeledOption[] }) {
  const { editors } = useSettingsMutations();
  const [newEmail, setNewEmail] = useState("");
  const role = access.is_admin ? "Settings admin" : access.is_editor ? "Settings editor" : "View only";
  const candidateEmails = useMemo(() => {
    const taken = new Set([...access.admins, ...access.editors].map((x) => x.toLowerCase()));
    return (knownEmails || []).filter((x) => !taken.has(x.value.toLowerCase()));
  }, [knownEmails, access.admins, access.editors]);

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
              <input
                className="input py-1.5 text-xs"
                list="settings-known-emails"
                placeholder={candidateEmails.length ? "Pick a teammate or type name@company.com" : "name@company.com"}
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
              />
              {candidateEmails.length > 0 && (
                <datalist id="settings-known-emails">
                  {candidateEmails.map((x) => {
                    const prefix = `${x.value} — `;
                    const hint = x.label.startsWith(prefix) ? x.label.slice(prefix.length) : "";
                    return <option key={x.value} value={x.value}>{hint}</option>;
                  })}
                </datalist>
              )}
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

/* ----------------------------------------------------------- specialized dropdown editors */

const CUSTOM = "__custom__";

interface GroupedChoice {
  value: string;
  label: string;
  group?: string;
}

/** Dropdown of known presets / discovered values, with a "Custom…" option that reveals an input. */
function ChoiceWithCustomEditor({
  s, draft, setDraft, options, emptyLabel, customLabel, customPlaceholder, inputType = "text",
}: {
  s: SettingItem;
  draft: string;
  setDraft: (v: string) => void;
  options: GroupedChoice[];
  emptyLabel?: string;
  customLabel: string;
  customPlaceholder: string;
  inputType?: "text" | "number";
}) {
  const matched = useMemo(() => {
    const trimmed = draft.trim();
    if (s.nullable && trimmed === "") return "";
    if (inputType === "number") {
      if (trimmed === "" || Number.isNaN(Number(trimmed))) return null;
      const hit = options.find((o) => Number(o.value) === Number(trimmed));
      return hit ? hit.value : null;
    }
    if (s.kind === "email") {
      const hit = options.find((o) => o.value.toLowerCase() === trimmed.toLowerCase());
      return hit ? hit.value : null;
    }
    const hit = options.find((o) => o.value === trimmed);
    return hit ? hit.value : null;
  }, [draft, options, s.nullable, s.kind, inputType]);

  const [forceCustom, setForceCustom] = useState(false);
  const isCustom = forceCustom || matched === null;
  const selectValue = isCustom ? CUSTOM : (matched ?? "");

  const ungrouped = options.filter((o) => !o.group);
  const groupNames = [...new Set(options.map((o) => o.group).filter(Boolean))] as string[];

  return (
    <div className="flex flex-col gap-2 sm:flex-row">
      <select
        className="input cursor-pointer py-1.5 text-xs sm:max-w-md"
        value={selectValue}
        onChange={(e) => {
          const val = e.target.value;
          if (val === CUSTOM) {
            setForceCustom(true);
          } else {
            setForceCustom(false);
            setDraft(val);
          }
        }}
      >
        {s.nullable && <option value="">{emptyLabel || "Not set (use default)"}</option>}
        {ungrouped.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        {groupNames.map((g) => (
          <optgroup key={g} label={g}>
            {options.filter((o) => o.group === g).map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </optgroup>
        ))}
        <option value={CUSTOM}>{customLabel}</option>
      </select>
      {isCustom && (
        <input
          className="input py-1.5 font-mono text-xs sm:max-w-xs"
          type={inputType}
          step={s.kind === "int" ? 1 : "any"}
          min={s.min ?? undefined}
          max={s.max ?? undefined}
          value={draft}
          onChange={(e) => {
            setForceCustom(true);
            setDraft(e.target.value);
          }}
          placeholder={customPlaceholder}
          autoFocus={forceCustom}
        />
      )}
    </div>
  );
}

/** Multi-select chip + dropdown picker for BigQuery locations (regions, include, exclude). */
function RegionListEditor({
  draft, setDraft, regions, homeRegion, excludeHome,
}: {
  draft: string;
  setDraft: (v: string) => void;
  regions: RegionOption[];
  homeRegion?: string;
  excludeHome?: boolean;
}) {
  const selected = useMemo(
    () => [...new Set(draft.split(/[,\s]+/).map((x) => x.trim().toLowerCase().replace(/^region-/, "")).filter(Boolean))],
    [draft],
  );
  const [showCustom, setShowCustom] = useState(false);
  const [customVal, setCustomVal] = useState("");

  const catalog = regions.length ? regions : FALLBACK_REGIONS;
  const labelMap = useMemo(() => Object.fromEntries(catalog.map((r) => [r.value, r.label])), [catalog]);
  const home = (homeRegion || catalog.find((r) => r.home)?.value || "us").toLowerCase();

  const update = (next: string[]) => setDraft(next.join(", "));
  const add = (raw: string) => {
    const clean = raw.trim().toLowerCase().replace(/^region-/, "");
    if (!clean || (excludeHome && clean === home)) return;
    if (!selected.includes(clean)) update([...selected, clean]);
  };
  const remove = (code: string) => update(selected.filter((x) => x !== code));

  const available = catalog.filter((r) => !selected.includes(r.value) && !(excludeHome && (r.home || r.value === home)));
  const discovered = available.filter((r) => r.discovered);
  const rest = available.filter((r) => !r.discovered);
  const restGroups = [...new Set(rest.map((r) => r.group))];

  const fmtRegionOption = (r: RegionOption) => {
    const extra: string[] = [];
    if (r.dataset_count != null && r.dataset_count > 0) extra.push(`${r.dataset_count} dataset${r.dataset_count === 1 ? "" : "s"}`);
    if (r.home) extra.push("home");
    return `${r.value} · ${r.label}${extra.length ? ` (${extra.join(" · ")})` : ""}`;
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {selected.length > 0 ? selected.map((code) => {
          const human = labelMap[code] || regionName(code);
          return (
            <Badge key={code} className="bg-sky-500/10 font-mono text-sky-700 ring-sky-500/30 dark:text-sky-300">
              <span>{code}</span>
              {human && human !== code && <span className="font-sans text-[10px] text-zinc-500 dark:text-zinc-400">· {human}</span>}
              <button
                type="button"
                title={`Remove ${code}`}
                className="ml-0.5 rounded hover:text-rose-400"
                onClick={() => remove(code)}
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          );
        }) : <span className="text-xs italic text-zinc-400">No regions selected</span>}
      </div>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <select
          className="input cursor-pointer py-1.5 text-xs sm:max-w-sm"
          value={showCustom ? CUSTOM : ""}
          onChange={(e) => {
            const v = e.target.value;
            if (v === CUSTOM) {
              setShowCustom(true);
            } else if (v) {
              setShowCustom(false);
              add(v);
            }
          }}
        >
          <option value="">+ Add a BigQuery location…</option>
          {discovered.length > 0 && (
            <optgroup label="Found in your warehouse">
              {discovered.map((r) => <option key={r.value} value={r.value}>{fmtRegionOption(r)}</option>)}
            </optgroup>
          )}
          {restGroups.map((g) => (
            <optgroup key={g} label={g}>
              {rest.filter((r) => r.group === g).map((r) => (
                <option key={r.value} value={r.value}>{fmtRegionOption(r)}</option>
              ))}
            </optgroup>
          ))}
          <option value={CUSTOM}>Other location (enter code)…</option>
        </select>

        {showCustom && (
          <div className="flex items-center gap-1.5">
            <input
              className="input py-1.5 font-mono text-xs sm:w-44"
              placeholder="e.g. europe-west10"
              value={customVal}
              onChange={(e) => setCustomVal(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  add(customVal);
                  setCustomVal("");
                  setShowCustom(false);
                }
              }}
            />
            <Button
              size="sm"
              variant="secondary"
              type="button"
              onClick={() => {
                add(customVal);
                setCustomVal("");
                setShowCustom(false);
              }}
            >
              Add
            </Button>
          </div>
        )}
        {excludeHome && <span className="text-[11px] text-zinc-400">Home location ({home}) is always collected.</span>}
      </div>
    </div>
  );
}

/** Per-column dropdown editor for HR hierarchy & service-account owner column mappings. */
function ColumnMapEditor({
  s, mapDraft, setMapDraft, yamlMode, setYamlMode, yamlDraft, setYamlDraft, allSettings,
}: {
  s: SettingItem;
  mapDraft: Record<string, string | null>;
  setMapDraft: React.Dispatch<React.SetStateAction<Record<string, string | null>>>;
  yamlMode: boolean;
  setYamlMode: (v: boolean) => void;
  yamlDraft: string;
  setYamlDraft: (v: string) => void;
  allSettings: Record<string, SettingItem>;
}) {
  const { email } = useReviewer();
  const tableSetting = s.table_key ? allSettings[s.table_key] : undefined;
  const configuredTable = String(tableSetting?.value || "").trim();
  const colsQ = useQuery({
    queryKey: ["table-columns", configuredTable, s.key, email],
    queryFn: () => fetchTableColumns({ table: configuredTable || undefined, key: s.key, principal: email }),
    staleTime: 60 * 1000,
  });
  const liveCols = colsQ.data?.columns || [];
  const colNames = useMemo(() => new Set(liveCols.map((c) => c.name)), [liveCols]);
  const [customFields, setCustomFields] = useState<Record<string, boolean>>({});

  const fields = s.fields || [];
  const shortTable = (colsQ.data?.table || configuredTable).split(".").slice(-2).join(".");

  const setField = (k: string, mode: string, val?: string) => {
    setMapDraft((prev) => {
      const next = { ...prev };
      if (mode === "__default__") {
        delete next[k];
      } else if (mode === "__null__") {
        if (s.key === "service_account_owner_columns") delete next[k];
        else next[k] = null;
      } else if (mode === "") {
        delete next[k];
      } else {
        next[k] = val ?? "";
      }
      return next;
    });
  };

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-zinc-500">
        <div>
          {colsQ.isLoading ? (
            <span>Reading table columns…</span>
          ) : configuredTable && colsQ.data?.ok ? (
            <span className="text-emerald-600 dark:text-emerald-400">
              ✓ Showing {liveCols.length} live columns from <span className="font-mono">{configuredTable}</span>
            </span>
          ) : configuredTable && colsQ.data?.error ? (
            <span className="text-amber-500">Couldn't read {configuredTable}: {colsQ.data.error}</span>
          ) : colsQ.data?.is_default && liveCols.length > 0 ? (
            <span>
              No external <span className="font-mono">{s.table_key}</span> set above — showing built-in{" "}
              <span className="font-mono">{shortTable}</span> columns as reference.
            </span>
          ) : (
            <span>Pick each column from your table, or choose "Not in our table" to skip optional columns.</span>
          )}
        </div>
        <button
          type="button"
          className="inline-flex items-center gap-1 text-[11px] font-medium text-sky-500 hover:text-sky-400"
          onClick={() => {
            if (!yamlMode) {
              setYamlDraft(
                Object.entries(mapDraft).map(([k, x]) => `${k}: ${x === null ? "null" : x}`).join("\n"),
              );
            }
            setYamlMode(!yamlMode);
          }}
        >
          {yamlMode ? <><ListFilter className="h-3 w-3" /> Use dropdowns</> : <><Code2 className="h-3 w-3" /> Raw YAML</>}
        </button>
      </div>

      {yamlMode ? (
        <textarea
          className="input min-h-[84px] py-1.5 font-mono text-xs"
          value={yamlDraft}
          onChange={(e) => setYamlDraft(e.target.value)}
          placeholder="user_email: email"
        />
      ) : (
        <div className="grid gap-2.5 sm:grid-cols-2">
          {fields.map((f) => {
            const hasKey = Object.prototype.hasOwnProperty.call(mapDraft, f.key);
            const rawVal = mapDraft[f.key];
            const forced = !!customFields[f.key];

            let selectVal: string;
            if (forced) {
              selectVal = CUSTOM;
            } else if (!hasKey) {
              selectVal = f.default_col ? "__default__" : (f.required ? "" : "__null__");
            } else if (rawVal === null) {
              selectVal = "__null__";
            } else if (f.default_col && rawVal === f.default_col) {
              selectVal = "__default__";
            } else if (rawVal && colNames.has(rawVal)) {
              selectVal = `col:${rawVal}`;
            } else {
              selectVal = CUSTOM;
            }

            return (
              <div key={f.key} className="rounded-md border border-zinc-200/80 bg-white/60 p-2 dark:border-ink-800 dark:bg-ink-950/50">
                <div className="mb-1 flex items-center justify-between gap-1 text-xs">
                  <span className="font-medium text-zinc-700 dark:text-zinc-200">
                    {f.label} <span className="font-mono text-[10px] text-zinc-400">({f.key})</span>
                  </span>
                  {f.required && <Badge className="text-[9px] text-amber-600 ring-amber-500/30 dark:text-amber-300">required</Badge>}
                </div>
                <select
                  className="input cursor-pointer py-1 text-xs"
                  value={selectVal}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === CUSTOM) {
                      setCustomFields((p) => ({ ...p, [f.key]: true }));
                      if (!hasKey || rawVal === null) setField(f.key, "val", f.hint || f.default_col || "");
                    } else if (v.startsWith("col:")) {
                      setCustomFields((p) => ({ ...p, [f.key]: false }));
                      setField(f.key, "val", v.slice(4));
                    } else {
                      setCustomFields((p) => ({ ...p, [f.key]: false }));
                      setField(f.key, v);
                    }
                  }}
                >
                  {f.default_col && <option value="__default__">Same column name ({f.default_col})</option>}
                  {!f.default_col && f.required && <option value="">Choose column in your table…</option>}
                  {!f.required && <option value="__null__">Not in our table (skip)</option>}
                  {liveCols.length > 0 && (
                    <optgroup label={configuredTable ? `Columns in ${shortTable}` : `Columns in ${shortTable} (default)`}>
                      {liveCols.map((c) => (
                        <option key={c.name} value={`col:${c.name}`}>{c.name} · {c.type}</option>
                      ))}
                    </optgroup>
                  )}
                  <option value={CUSTOM}>Enter column name manually…</option>
                </select>
                {selectVal === CUSTOM && (
                  <input
                    className="input mt-1.5 py-1 font-mono text-xs"
                    value={typeof rawVal === "string" ? rawVal : ""}
                    onChange={(e) => {
                      setCustomFields((p) => ({ ...p, [f.key]: true }));
                      setField(f.key, "val", e.target.value);
                    }}
                    placeholder={f.hint || f.default_col || "your_column_name"}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const CLASS_WEIGHT_LABELS: Record<string, string> = {
  "1": "Class 1 · Clustering & config",
  "2": "Class 2 · Materialized views",
  "3": "Class 3 · Rebuilds & Editions",
  "4": "Class 4 · SQL PRs",
};

const WEIGHT_CHOICES = [
  { v: 0.5, label: "0.5× (rank higher)" },
  { v: 1.0, label: "1.0× (baseline)" },
  { v: 1.3, label: "1.3× (slight penalty)" },
  { v: 1.5, label: "1.5× (moderate penalty)" },
  { v: 2.0, label: "2.0× (half score)" },
  { v: 2.5, label: "2.5×" },
  { v: 3.0, label: "3.0× (strict penalty)" },
  { v: 4.0, label: "4.0×" },
];

/** Preset + per-class dropdown editor for risk_weights. */
function WeightsEditor({
  s, weights, setWeights,
}: {
  s: SettingItem;
  weights: Record<string, string>;
  setWeights: React.Dispatch<React.SetStateAction<Record<string, string>>>;
}) {
  const presets = s.presets || [];
  const matchedIdx = useMemo(() => {
    return presets.findIndex((p) => {
      const pv = p.value as Record<string, number>;
      return ["1", "2", "3", "4"].every((k) => Number(weights[k]) === Number(pv[k]));
    });
  }, [presets, weights]);
  const [customCls, setCustomCls] = useState<Record<string, boolean>>({});

  return (
    <div className="space-y-2.5">
      <select
        className="input cursor-pointer py-1.5 text-xs sm:max-w-lg"
        value={matchedIdx >= 0 ? String(matchedIdx) : CUSTOM}
        onChange={(e) => {
          const idx = Number(e.target.value);
          if (!Number.isNaN(idx) && presets[idx]) {
            const pv = presets[idx].value as Record<string, number>;
            setCustomCls({});
            setWeights({ "1": String(pv["1"]), "2": String(pv["2"]), "3": String(pv["3"]), "4": String(pv["4"]) });
          }
        }}
      >
        {presets.map((p, i) => <option key={i} value={String(i)}>{p.label}</option>)}
        <option value={CUSTOM}>Custom weights per class…</option>
      </select>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {(["1", "2", "3", "4"] as const).map((cls) => {
          const cur = weights[cls] ?? "1.0";
          const hit = WEIGHT_CHOICES.find((w) => w.v === Number(cur) && cur.trim() !== "");
          const isCustom = !!customCls[cls] || !hit;
          return (
            <div key={cls} className="rounded-md border border-zinc-200/80 bg-white/60 p-2 dark:border-ink-800 dark:bg-ink-950/50">
              <div className="mb-1 text-[11px] font-medium text-zinc-700 dark:text-zinc-200">{CLASS_WEIGHT_LABELS[cls]}</div>
              <select
                className="input cursor-pointer py-1 text-xs"
                value={isCustom ? CUSTOM : String(hit.v)}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === CUSTOM) {
                    setCustomCls((p) => ({ ...p, [cls]: true }));
                  } else {
                    setCustomCls((p) => ({ ...p, [cls]: false }));
                    setWeights((prev) => ({ ...prev, [cls]: v }));
                  }
                }}
              >
                {WEIGHT_CHOICES.map((w) => <option key={w.v} value={String(w.v)}>{w.label}</option>)}
                <option value={CUSTOM}>Custom weight…</option>
              </select>
              {isCustom && (
                <input
                  className="input mt-1.5 py-1 font-mono text-xs"
                  type="number"
                  step="0.1"
                  min={0.1}
                  value={cur}
                  onChange={(e) => {
                    setCustomCls((p) => ({ ...p, [cls]: true }));
                    setWeights((prev) => ({ ...prev, [cls]: e.target.value }));
                  }}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const TABLE_SUGGEST_RE: Record<string, RegExp> = {
  employee_hierarchy_table: /hierarch|employ|people|person|staff|\bhr\b|user|org|dir/i,
  service_account_owner_table: /service_account|\bsa_|owner|principal|identit/i,
  billing_export_table: /billing|gcp_billing_export|invoice|cost/i,
};

function buildTableChoices(key: string, tables: TableOption[], opsDataset = "optimizer_ops"): GroupedChoice[] {
  const rx = TABLE_SUGGEST_RE[key];
  const skipBuiltIn = key === "employee_hierarchy_table" || key === "service_account_owner_table";
  const suggested: GroupedChoice[] = [];
  const other: GroupedChoice[] = [];
  for (const t of tables) {
    if (skipBuiltIn && t.dataset === opsDataset) continue;
    const label = t.region ? `${t.value} (${t.region})` : t.value;
    if (rx && rx.test(`${t.dataset}.${t.table}`)) {
      suggested.push({ value: t.value, label, group: "Suggested tables" });
    } else {
      other.push({ value: t.value, label, group: suggested.length ? "Other tables in warehouse" : "Tables in warehouse" });
    }
  }
  return [...suggested, ...other];
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

const EMPTY_LABELS: Record<string, string> = {
  asset_scope: "Not set — this project only (default)",
  target_dataset: "Not set — collect every dataset (default)",
  reservation_admin_project: "Not set — use Enterprise list price",
  employee_hierarchy_table: "Not set — use built-in optimizer_ops.employee_hierarchy (default)",
  service_account_owner_table: "Not set — use built-in optimizer_ops.service_account_owners (default)",
  billing_export_table: "Not set",
  default_owner: "Not set — data-platform-admin@<project> (default)",
  max_bytes_billed_per_query: "Not set — no byte cap (default)",
};

function Editor({
  s, onDone, options, allSettings,
}: {
  s: SettingItem;
  onDone: () => void;
  options?: SettingsOptions;
  allSettings: Record<string, SettingItem>;
}) {
  const { save } = useSettingsMutations();
  const [draft, setDraft] = useState(toDraft(s));
  const [mode, setMode] = useState(s.kind === "regions_mode" && s.value === "auto" ? "auto" : "list");
  const [mapDraft, setMapDraft] = useState<Record<string, string | null>>(() => {
    if (s.kind !== "map" || !s.value || typeof s.value !== "object") return {};
    return Object.fromEntries(
      Object.entries(s.value as Record<string, unknown>).map(([k, v]) => [k, v === null ? null : String(v)]),
    );
  });
  const [yamlMode, setYamlMode] = useState(false);
  const [weightsDraft, setWeightsDraft] = useState<Record<string, string>>(() => {
    const base = (s.value && typeof s.value === "object" ? s.value : {}) as Record<string, unknown>;
    return {
      "1": String(base["1"] ?? 1.0),
      "2": String(base["2"] ?? 1.3),
      "3": String(base["3"] ?? 2.0),
      "4": String(base["4"] ?? 1.5),
    };
  });
  const [note, setNote] = useState("");

  const submit = () => {
    let value: unknown = draft;
    if (s.kind === "regions_mode") {
      value = mode === "auto" ? "auto" : draft;
    } else if (s.kind === "map") {
      value = yamlMode ? draft : mapDraft;
    } else if (s.kind === "weights") {
      value = weightsDraft;
    }
    save.mutate({ key: s.key, value, note: note || undefined }, { onSuccess: onDone });
  };

  const presetChoices: GroupedChoice[] = useMemo(
    () => (s.presets || []).map((p) => ({ value: String(p.value), label: p.label })),
    [s.presets],
  );
  const opsDataset = String(allSettings["ops_dataset"]?.value || "optimizer_ops");

  let field: React.ReactNode;
  if (s.kind === "bool") {
    field = (
      <select className="input cursor-pointer py-1.5 text-xs sm:max-w-xs" value={draft} onChange={(e) => setDraft(e.target.value)}>
        <option value="on">On — Enabled</option>
        <option value="off">Off — Disabled</option>
      </select>
    );
  } else if (s.kind === "enum") {
    const enumChoices = presetChoices.length
      ? presetChoices
      : (s.choices || []).map((c) => ({ value: c, label: c }));
    field = (
      <select className="input cursor-pointer py-1.5 text-xs sm:max-w-lg" value={draft} onChange={(e) => setDraft(e.target.value)}>
        {enumChoices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
    );
  } else if (s.kind === "region_list" || s.kind === "regions_mode") {
    field = (
      <RegionListEditor
        draft={draft}
        setDraft={setDraft}
        regions={options?.regions || []}
        homeRegion={options?.home_region}
        excludeHome={s.key === "regions_exclude"}
      />
    );
  } else if (s.kind === "map") {
    field = (
      <ColumnMapEditor
        s={s}
        mapDraft={mapDraft}
        setMapDraft={setMapDraft}
        yamlMode={yamlMode}
        setYamlMode={setYamlMode}
        yamlDraft={draft}
        setYamlDraft={setDraft}
        allSettings={allSettings}
      />
    );
  } else if (s.kind === "weights") {
    field = <WeightsEditor s={s} weights={weightsDraft} setWeights={setWeightsDraft} />;
  } else if (s.kind === "scope") {
    field = (
      <ChoiceWithCustomEditor
        s={s}
        draft={draft}
        setDraft={setDraft}
        options={options?.scopes || []}
        emptyLabel={EMPTY_LABELS[s.key]}
        customLabel="Enter another scope (organizations/ID or folders/ID)…"
        customPlaceholder="organizations/123456789"
      />
    );
  } else if (s.kind === "dataset") {
    field = (
      <ChoiceWithCustomEditor
        s={s}
        draft={draft}
        setDraft={setDraft}
        options={options?.datasets || []}
        emptyLabel={EMPTY_LABELS[s.key]}
        customLabel="Enter another dataset name…"
        customPlaceholder="my_dataset"
      />
    );
  } else if (s.kind === "project") {
    field = (
      <ChoiceWithCustomEditor
        s={s}
        draft={draft}
        setDraft={setDraft}
        options={options?.projects || []}
        emptyLabel={EMPTY_LABELS[s.key]}
        customLabel="Enter another project ID…"
        customPlaceholder="my-reservation-admin-project"
      />
    );
  } else if (s.kind === "table") {
    field = (
      <ChoiceWithCustomEditor
        s={s}
        draft={draft}
        setDraft={setDraft}
        options={buildTableChoices(s.key, options?.tables || [], opsDataset)}
        emptyLabel={EMPTY_LABELS[s.key]}
        customLabel="Enter another project.dataset.table…"
        customPlaceholder="project.dataset.table"
      />
    );
  } else if (s.kind === "email") {
    field = (
      <ChoiceWithCustomEditor
        s={s}
        draft={draft}
        setDraft={setDraft}
        options={options?.emails || []}
        emptyLabel={EMPTY_LABELS[s.key]}
        customLabel="Enter another email address…"
        customPlaceholder="owner@company.com"
      />
    );
  } else if (s.kind === "model") {
    field = (
      <ChoiceWithCustomEditor
        s={s}
        draft={draft}
        setDraft={setDraft}
        options={presetChoices}
        customLabel="Enter another Gemini model…"
        customPlaceholder="gemini-3.8-flash"
      />
    );
  } else if (s.kind === "int" || s.kind === "float") {
    if (presetChoices.length > 0) {
      field = (
        <ChoiceWithCustomEditor
          s={s}
          draft={draft}
          setDraft={setDraft}
          options={presetChoices}
          emptyLabel={EMPTY_LABELS[s.key]}
          customLabel="Custom value…"
          customPlaceholder={s.min != null && s.max != null ? `${s.min} – ${s.max}` : "Enter number"}
          inputType="number"
        />
      );
    } else {
      field = (
        <input
          className="input py-1.5 font-mono text-xs sm:max-w-xs"
          type="number"
          value={draft}
          step={s.kind === "int" ? 1 : "any"}
          min={s.min ?? undefined}
          max={s.max ?? undefined}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={s.nullable ? "empty = not set" : ""}
        />
      );
    }
  } else {
    field = (
      <input
        className="input py-1.5 font-mono text-xs"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={s.nullable ? "empty = not set" : ""}
      />
    );
  }

  return (
    <div className="mt-2 space-y-2 rounded-lg border border-sky-500/30 bg-sky-500/5 p-3">
      {s.kind === "regions_mode" && (
        <div className="flex flex-wrap gap-4 text-xs">
          <label className="flex cursor-pointer items-center gap-1.5">
            <input type="radio" checked={mode === "auto"} onChange={() => setMode("auto")} />
            <span>Find automatically (Cloud Asset Inventory)</span>
          </label>
          <label className="flex cursor-pointer items-center gap-1.5">
            <input
              type="radio"
              checked={mode === "list"}
              onChange={() => {
                setMode("list");
                if (draft.trim().toLowerCase() === "auto") {
                  setDraft(options?.home_region || "us");
                }
              }}
            />
            <span>Fixed list of regions</span>
          </label>
        </div>
      )}
      {!(s.kind === "regions_mode" && mode === "auto") && field}
      <input
        className="input py-1.5 text-xs"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Why are you changing this? (optional, shown in history)"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" onClick={submit} loading={save.isPending}>Save</Button>
        <Button size="sm" variant="ghost" onClick={onDone}>Cancel</Button>
        <span className="text-[11px] text-zinc-500">Takes effect: {s.applies_text}</span>
      </div>
    </div>
  );
}

function SettingRow({
  s, options, allSettings,
}: {
  s: SettingItem;
  options?: SettingsOptions;
  allSettings: Record<string, SettingItem>;
}) {
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
      {editing && <Editor s={s} onDone={() => setEditing(false)} options={options} allSettings={allSettings} />}
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
  const isEditor = !!q.data?.access?.is_editor;
  const optQ = useQuery({
    queryKey: ["settings-options", email],
    queryFn: () => fetchSettingsOptions(email),
    enabled: isEditor,
    staleTime: 2 * 60 * 1000,
  });
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
      {data?.access && <AccessPanel access={data.access} knownEmails={optQ.data?.emails} />}
      <section className="panel space-y-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">All settings{data ? ` (${data.settings.length})` : ""}</h2>
            <p className="mt-0.5 max-w-3xl text-xs text-zinc-500">
              Every setting the tool reads, what it's set to now, and where that value comes from.{" "}
              {editableCount > 0
                ? <>You can change <b>{editableCount}</b> of them: click the ✏️ next to a value to pick from known options or enter a custom value. Changes are checked before saving and logged below.</>
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
                      {rows.filter((s) => s.group === g).map((s) => (
                        <SettingRow key={s.key} s={s} options={optQ.data} allSettings={labels} />
                      ))}
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

/* -------------------------------------------------------------------- on-demand scan */

const SCAN_STEPS: { id: "init" | "collect" | "rules"; label: string }[] = [
  { id: "init", label: "1. Schema check" },
  { id: "collect", label: "2. Collect regions & queries" },
  { id: "rules", label: "3. Score 14 rules" },
];

function stepRank(step: ScanStatus["step"]): number {
  if (step === "init" || step === "starting") return 0;
  if (step === "collect") return 1;
  if (step === "rules") return 2;
  if (step === "done") return 3;
  return -1;
}

export function ScanNowPanel({
  onViewRecommendations,
  onOpenSettings,
  compact = false,
}: {
  onViewRecommendations?: () => void;
  onOpenSettings?: () => void;
  compact?: boolean;
}) {
  const { email } = useReviewer();
  const qc = useQueryClient();
  const [logsOpen, setLogsOpen] = useState(false);

  const q = useQuery({
    queryKey: ["scan-status", email],
    queryFn: () => fetchScanStatus({ principal: email }),
    refetchInterval: (query) => (query.state.data?.state === "running" ? 1800 : false),
    staleTime: 10 * 1000,
  });

  const scan = q.data;
  const isRunning = scan?.state === "running";
  const prevStateRef = useRef<ScanStatus["state"] | undefined>(undefined);

  // Refresh dashboard, setup-check and dropdown options automatically when a scan finishes.
  useEffect(() => {
    const prev = prevStateRef.current;
    const cur = scan?.state;
    prevStateRef.current = cur;
    if (prev === "running" && cur === "succeeded") {
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["settings-options"] });
      qc.invalidateQueries({ queryKey: ["setup-check"] });
      toast.success("BigQuery scan complete — recommendations updated!");
    } else if (prev === "running" && cur === "failed") {
      toast.error(scan?.error || "BigQuery scan failed.");
      setLogsOpen(true);
    }
  }, [scan?.state, scan?.error, qc]);

  // Keep an active HTTP connection open while an in-process scan runs on Cloud Run so CPU isn't throttled.
  useEffect(() => {
    if (!isRunning || scan?.mode !== "in_process") return;
    let cancelled = false;
    let curSeq = scan?.seq ?? 0;
    const loop = async () => {
      while (!cancelled) {
        try {
          const next = await fetchScanStatus({ principal: email, wait: true, seq: curSeq });
          if (cancelled) break;
          curSeq = next.seq;
          qc.setQueryData(["scan-status", email], next);
          if (next.state !== "running" || next.mode !== "in_process") break;
        } catch {
          await new Promise((r) => setTimeout(r, 2000));
        }
      }
    };
    void loop();
    return () => {
      cancelled = true;
    };
  }, [isRunning, scan?.mode, email, qc]);

  const start = useMutation({
    mutationFn: () => triggerScan(email),
    onSuccess: (res) => {
      qc.setQueryData(["scan-status", email], res.scan);
      if (res.already_running) {
        toast.info("A BigQuery scan is already running.");
      } else {
        toast.success("Started BigQuery scan — watching progress live.");
      }
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const rank = stepRank(scan?.step ?? null);
  const canTrigger = scan ? scan.can_trigger : true;
  const telem = scan?.telemetry;
  const logLines = scan?.log_tail || [];

  return (
    <section className={cn("panel space-y-3.5 p-5", isRunning && "border-sky-500/40 ring-1 ring-sky-500/20")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="flex flex-wrap items-center gap-2 text-base font-semibold">
            <Sparkles className="h-5 w-5 text-sky-400" />
            {compact ? "Ready for your first BigQuery scan" : "BigQuery scan & recommendations"}
            {isRunning && (
              <Badge className="bg-sky-500/15 text-sky-600 ring-sky-500/30 dark:text-sky-300">
                <RefreshCw className="h-3 w-3 animate-spin" /> Running
              </Badge>
            )}
            {!isRunning && scan?.state === "succeeded" && (
              <Badge className="bg-emerald-500/10 text-emerald-600 ring-emerald-500/30 dark:text-emerald-300">
                <CheckCircle2 className="h-3 w-3" /> Up to date
              </Badge>
            )}
          </h2>
          <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-zinc-500">
            {compact
              ? "Review your permissions and optional owner tables in Settings, or click Run scan now to pull BigQuery query history and generate savings cards."
              : "Discovers your BigQuery regions, pulls query & table history, and runs all 14 optimization rules using the settings saved on this page. Read-only on your tables — nothing is changed until you approve a card."}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {onOpenSettings && (
            <Button variant="secondary" size="sm" onClick={onOpenSettings}>
              Open Settings
            </Button>
          )}
          {!isRunning && scan?.state === "succeeded" && onViewRecommendations && (
            <Button variant="secondary" size="sm" onClick={onViewRecommendations}>
              View recommendations <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          )}
          <Button
            variant="primary"
            size="sm"
            disabled={!canTrigger || isRunning}
            loading={start.isPending || isRunning}
            onClick={() => start.mutate()}
            title={!canTrigger ? "Settings admins and editors can trigger scans" : "Collect BigQuery telemetry and refresh recommendations"}
          >
            <Play className="h-3.5 w-3.5" />
            {isRunning ? "Scanning…" : "Run scan now"}
          </Button>
        </div>
      </div>

      {/* Idle telemetry summary or permission note */}
      {!isRunning && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
          {telem?.has_telemetry ? (
            <>
              <span className="text-emerald-600 dark:text-emerald-400">
                ✓ {telem.jobs_count.toLocaleString()} queries indexed
              </span>
              {telem.last_job_at && <span>· latest query {ago(telem.last_job_at)}</span>}
              <span>· runs automatically every night (02:00 UTC)</span>
            </>
          ) : (
            <span className="text-amber-600 dark:text-amber-300">
              ⓘ First scan hasn&apos;t run yet — check Setup check &amp; settings, then click <b>Run scan now</b> (first run backfills up to 180 days).
            </span>
          )}
          {scan && !canTrigger && (
            <span className="inline-flex items-center gap-1 text-zinc-400">
              <Lock className="h-3 w-3" /> Ask a settings admin or editor to run an on-demand scan.
            </span>
          )}
        </div>
      )}

      {/* Active progress stepper */}
      {isRunning && (
        <div className="space-y-2.5 rounded-lg border border-sky-500/25 bg-sky-500/5 p-3">
          <div className="flex flex-wrap items-center gap-2">
            {SCAN_STEPS.map((st, idx) => {
              const done = scan?.mode === "cloud_run_job" ? false : rank > idx;
              const active = scan?.mode === "cloud_run_job" ? idx >= 1 : rank === idx;
              return (
                <span
                  key={st.id}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium ring-1 ring-inset",
                    done
                      ? "bg-emerald-500/15 text-emerald-700 ring-emerald-500/30 dark:text-emerald-300"
                      : active
                        ? "bg-sky-500/15 text-sky-700 ring-sky-500/40 dark:text-sky-200"
                        : "bg-zinc-500/5 text-zinc-400 ring-zinc-400/20",
                  )}
                >
                  {done ? <CheckCircle2 className="h-3 w-3" /> : active ? <RefreshCw className="h-3 w-3 animate-spin" /> : null}
                  {st.label}
                </span>
              );
            })}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-600 dark:text-zinc-300">
            <span>{scan?.step_label || "Scanning BigQuery…"}</span>
            <span className="text-[11px] text-zinc-400">
              {scan?.triggered_by ? `Started by ${scan.triggered_by}` : ""}{scan?.started_at ? ` · ${ago(scan.started_at)}` : ""}
            </span>
          </div>
        </div>
      )}

      {/* Error banner */}
      {!isRunning && scan?.state === "failed" && scan.error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-600 dark:text-rose-300">
          <div className="font-medium">Last scan failed</div>
          <div className="mt-0.5 font-mono text-[11px]">{scan.error}</div>
        </div>
      )}

      {/* Optional live log output / Cloud Run link */}
      {(logLines.length > 0 || scan?.log_uri) && (
        <div>
          <div className="flex flex-wrap items-center gap-3 text-xs">
            {logLines.length > 0 && (
              <button
                type="button"
                onClick={() => setLogsOpen(!logsOpen)}
                className="inline-flex items-center gap-1 font-medium text-sky-500 hover:text-sky-400"
              >
                <Terminal className="h-3.5 w-3.5" />
                {logsOpen ? "Hide scan log" : `Show scan log (${logLines.length})`}
                <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", logsOpen && "rotate-180")} />
              </button>
            )}
            {scan?.log_uri && (
              <a
                href={scan.log_uri}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 font-medium text-sky-500 hover:text-sky-400"
              >
                Open Cloud Run job logs <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>
          {logsOpen && logLines.length > 0 && (
            <pre className="mt-2 max-h-48 overflow-auto rounded-lg border border-zinc-200 bg-zinc-950 p-3 font-mono text-[11px] leading-relaxed text-zinc-200 dark:border-ink-800">
              {logLines.join("\n")}
            </pre>
          )}
        </div>
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
          <p className="text-xs text-zinc-500">Check permissions, change everyday settings, and run a BigQuery scan right from the browser.</p>
        </div>
      </div>
      <ScanNowPanel onViewRecommendations={onBack} />
      <SetupCheckPanel />
      <SettingsPanel />
    </div>
  );
}
