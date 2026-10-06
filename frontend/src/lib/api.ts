// Typed client for the Flask JSON API (review_app/main.py)

export type Dict = Record<string, any>;

export interface StateEvent {
  state: string;
  at: string;
  actor?: string | null;
  note?: string | null;
}

export interface ChangeSet {
  change_set_id: string;
  apply_class: number;
  rule_ids: string[];
  state: string;
  source?: string;
  execution_route?: string;
  target_project?: string;
  target_dataset?: string;
  target_table?: string | null;
  target_region?: string;
  finding_summary?: string;
  net_monthly_value_usd?: number;
  gross_monthly_savings_usd?: number;
  confidence?: number;
  score?: number;
  risk_notes?: string[];
  approvals?: { principal: string; at: string; role: string }[];
  state_history?: StateEvent[];
  created_at?: string;
  expires_at?: string;
  observation_days?: number;
  snooze_until?: string | null;
  evidence: Dict;
  proposed: Dict;
  factors: Dict;
  director_name?: string;
  department?: string;
  team_readers_count?: number;
  team_queries_count?: number;
  team_billed_gb?: number;
  /** How the Director was resolved (EMPLOYEE_HIERARCHY, SERVICE_ACCOUNT_OWNER, ...). */
  attribution_source?: string | null;
  /** FINOPS = billing / commitment / reservation / project-level (FinOps approvers only). */
  governance_scope?: "FINOPS" | "ENGINEERING";
  // enriched per list
  blocker_message?: string;
  regression_message?: string;
  rollback_message?: string;
  rollback_actor?: string;
  forensics_table?: string | null;
  rejection_reason?: string | null;
  handoff?: PrHandoff;
}

/** Class 4 PR hand-off package (optimizer/executor/pr_handoff.py). */
export interface PrHandoff {
  type?: string;
  handed_off_at?: string;
  branch?: string;
  file_path?: string;
  commit_message?: string;
  pr_title?: string;
  pr_body?: string;
  git_commands?: string;
  gh_command?: string;
  current_sql?: string;
  proposed_sql?: string;
  pr_url?: string | null;
}

export interface Receipt {
  change_set_id: string;
  rule_ids: string[];
  target_dataset?: string;
  target_table?: string | null;
  predicted_usd?: number;
  realized_usd?: string | number | null;
  realized_over_predicted?: number | null;
  state: string;
  applied_at?: string;
}

export interface Persona {
  email: string;
  label: string;
  role: string;
  is_finops?: boolean;
}

/** Who the server thinks is looking (review_app/main.py _scope_for_viewer). */
export interface Viewer {
  email: string;
  identity_source: "IAP" | "CLIENT_SELECTED" | "SERVER_CONFIG" | "LOCAL_GCLOUD" | "PLACEHOLDER" | string;
  is_finops: boolean;
  hidden_finops_items: number;
  can_switch_persona: boolean;
  finops_approvers_configured: boolean;
}

/** evidence.savings_math: how a card's number was computed. */
export interface SavingsMath {
  method?: string;
  formula?: string;
  billing_mix?: string;
  cost_source?: string;
  on_demand_monthly_usd?: number;
  reservation_monthly_usd?: number;
  attributed_monthly_usd?: number;
  reduction?: number;
  reservation_realization?: number;
  pool_key?: string;
  pool_spend_usd?: number;
  split_rule?: string;
  cap_applied?: string;
  demo_floor_applied?: boolean;
  measured_gross_usd?: number;
  demo_floor_usd?: number;
  demo_floor_inputs?: string[];
  repriced_from_usd?: number;
  note?: string;
  window_note?: string;
  [k: string]: unknown;
}

export interface Dashboard {
  cards: ChangeSet[];
  blocked_cards: ChangeSet[];
  regressed_cards: ChangeSet[];
  rolled_back_cards: ChangeSet[];
  handoff_cards: ChangeSet[];
  receipts: Receipt[];
  reasons: string[];
  reason_snooze_days: Record<string, number>;
  reviewer_email: string;
  personas: Persona[];
  viewer?: Viewer;
  kpis: {
    /** De-overlapped: cards claiming the same spend are compounded, not added, and the
     *  total is capped at the last 30 days' actual compute spend. */
    monthly_savings: number;
    annual_savings: number;
    monthly_savings_gross_sum?: number;
    overlap_removed_usd?: number;
    overlapping_cards?: number;
    /** True when the headline hit the actual-spend cap. */
    headline_capped_at_spend?: boolean;
    /** Actual compute spend, last 30 days (FinOps viewers only; null otherwise). */
    compute_spend_30d_usd?: number | null;
    /** Editions / byte-cap cards: what they claim vs what counts after the table/query fixes. */
    billing_cards_claimed_usd?: number | null;
    billing_cards_counted_usd?: number | null;
    demo_floor_cards?: number;
    legacy_estimate_cards?: number;
    finops_pending_count?: number;
    finops_monthly_savings?: number;
    engineering_pending_count?: number;
    engineering_monthly_savings?: number;
    pending_count: number;
    blocked_count: number;
    regressed_count: number;
    rolled_back_count: number;
    handoff_count?: number;
    directors_count: number;
    departments_count: number;
    applied_count: number;
    project_id: string;
    location: string;
    class_savings: Record<string, number>;
    class_pcts: Record<string, number>;
    class_counts: Record<string, number>;
  };
  w01: null | {
    tib: number; od: number; o1: number; o2: number;
    o1_pct: number; o2_pct: number; baseline: number; max: number;
    recommended_option?: number | null; billing_aware?: boolean;
  };
  directors: string[];
  projects: string[];
  datasets: string[];
}

export type DecisionAction = "approve" | "reject" | "reset" | "unsnooze" | "rollback" | "pr_merged";

export interface DecisionRequest {
  change_set_id: string;
  action: DecisionAction;
  principal: string;
  role?: string;
  selected_option?: number;
  reason?: string;
  category?: string;
  note?: string;
  pr_url?: string;
}

/** Error from the Flask API; `code` is e.g. FINOPS_PERMISSION_REQUIRED (HTTP 403). */
export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function asJson<T>(res: Response): Promise<T> {
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error page */
  }
  if (!res.ok || (body && body.ok === false)) {
    throw new ApiError((body && body.error) || `${res.status} ${res.statusText}`, res.status, body?.code);
  }
  return body as T;
}

/** The server scopes the dashboard to the viewer (FinOps cards only for FinOps approvers).
 *  `principal` is the persona picked in the header; the server honours it only in demo mode. */
export async function fetchDashboard(principal?: string): Promise<Dashboard> {
  const qs = principal ? `?principal=${encodeURIComponent(principal)}` : "";
  return asJson<Dashboard>(await fetch(`/api/dashboard${qs}`, { headers: { Accept: "application/json" } }));
}

export async function postDecision(req: DecisionRequest): Promise<{ ok: true; status: string; action: string }> {
  return asJson(
    await fetch("/api/decisions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  );
}

export interface ChatTurn {
  role: "user" | "ai";
  text: string;
  model?: string;
}

export async function askAssist(question: string, history: ChatTurn[], principal?: string): Promise<{ answer: string; model?: string }> {
  return asJson(
    await fetch("/api/finops-chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, principal, history: history.map(({ role, text }) => ({ role, text })) }),
    }),
  );
}
