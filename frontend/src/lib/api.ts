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
  kpis: {
    monthly_savings: number;
    annual_savings: number;
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

async function asJson<T>(res: Response): Promise<T> {
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error page */
  }
  if (!res.ok || (body && body.ok === false)) {
    throw new Error((body && body.error) || `${res.status} ${res.statusText}`);
  }
  return body as T;
}

export async function fetchDashboard(): Promise<Dashboard> {
  return asJson<Dashboard>(await fetch("/api/dashboard", { headers: { Accept: "application/json" } }));
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

export async function askAssist(question: string, history: ChatTurn[]): Promise<{ answer: string; model?: string }> {
  return asJson(
    await fetch("/api/finops-chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history: history.map(({ role, text }) => ({ role, text })) }),
    }),
  );
}
