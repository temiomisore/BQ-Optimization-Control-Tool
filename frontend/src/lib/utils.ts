import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { format, formatDistanceToNowStrict, parseISO } from "date-fns";
import type { ChangeSet } from "./api";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const usd = (n: number | null | undefined, digits = 0) =>
  `$${Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;

export const pct = (n: number | null | undefined) => `${Math.round(Number(n || 0) * 100)}%`;

export function fmtDate(iso?: string | null, withTime = false) {
  if (!iso) return "—";
  try {
    const d = parseISO(iso);
    return format(d, withTime ? "MMM dd, yyyy · HH:mm" : "MMM dd, yyyy");
  } catch {
    return iso;
  }
}

export function ago(iso?: string | null) {
  if (!iso) return "";
  try {
    return `${formatDistanceToNowStrict(parseISO(iso))} ago`;
  } catch {
    return "";
  }
}

export const titleize = (k: string) =>
  k.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());

export const CLASS_META: Record<number, { label: string; short: string; color: string; ring: string; dot: string }> = {
  1: { label: "Class 1 · Clustering & Config", short: "Class 1", color: "text-sky-300 bg-sky-500/10", ring: "ring-sky-500/30", dot: "bg-sky-400" },
  2: { label: "Class 2 · Storage", short: "Class 2", color: "text-amber-300 bg-amber-500/10", ring: "ring-amber-500/30", dot: "bg-amber-400" },
  3: { label: "Class 3 · Rebuilds & Editions", short: "Class 3", color: "text-rose-300 bg-rose-500/10", ring: "ring-rose-500/30", dot: "bg-rose-400" },
  4: { label: "Class 4 · SQL Rewrites", short: "Class 4", color: "text-violet-300 bg-violet-500/10", ring: "ring-violet-500/30", dot: "bg-violet-400" },
};

export function targetLabel(c: Pick<ChangeSet, "target_project" | "target_dataset" | "target_table" | "rule_ids">) {
  const ds = c.target_dataset;
  if ((c.rule_ids || []).includes("W-02") || ds === "HUMAN_ADHOC_GOVERNANCE") return `${c.target_project} · Human ad-hoc guardrail`;
  if (!ds || ds === "None" || ds === "PROJECT_WIDE_BILLING") return `${c.target_project} · Project-wide billing`;
  const t = c.target_table && c.target_table !== "None" ? `.${c.target_table}` : "";
  return `${ds}${t}`;
}

export function fullPath(c: ChangeSet) {
  const t = c.target_table && c.target_table !== "None" ? `.${c.target_table}` : "";
  return `${c.target_project}.${c.target_dataset}${t}`;
}

export const isW01 = (c: ChangeSet) => (c.rule_ids || []).includes("W-01") || c.proposed?.action === "CAPACITY_PRICING_MIGRATION";
export const isW02 = (c: ChangeSet) => (c.rule_ids || []).includes("W-02");
export const needsTwo = (c: ChangeSet) => c.apply_class === 3 || (c.risk_notes || []).includes("REQUIRES_TWO_PERSON_APPROVAL");

export function ddlFor(c: ChangeSet): string | null {
  return c.proposed?.generated_ddl || c.proposed?.sql || c.evidence?.underlying_sql || c.evidence?.optimized_query_sql || null;
}

export async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
