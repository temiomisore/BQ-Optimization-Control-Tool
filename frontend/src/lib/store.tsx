import React, { createContext, useContext, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { postDecision, type DecisionRequest, type Persona } from "./api";

interface ReviewerCtx {
  email: string;
  role: string;
  setReviewer: (email: string, role: string) => void;
}

const Ctx = createContext<ReviewerCtx>({ email: "", role: "approver", setReviewer: () => {} });

export function ReviewerProvider({ initial, children }: { initial: Persona | null; children: React.ReactNode }) {
  const [state, setState] = useState({ email: initial?.email || "", role: initial?.role || "approver" });
  React.useEffect(() => {
    if (initial && !state.email) setState({ email: initial.email, role: initial.role });
  }, [initial]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Ctx.Provider value={{ ...state, setReviewer: (email, role) => setState({ email, role }) }}>{children}</Ctx.Provider>
  );
}

export const useReviewer = () => useContext(Ctx);

const LABELS: Record<string, [string, string]> = {
  approve: ["Approving…", "Approval recorded"],
  reject: ["Rejecting…", "Rejected & snoozed"],
  reset: ["Resetting…", "Moved back to the review queue"],
  unsnooze: ["Un-snoozing…", "Back in the review queue"],
  rollback: ["Rolling back… (can take up to ~3 min for reservations)", "Rolled back"],
};

/** Every reviewer action goes through here: POST /api/decisions, toast, refresh dashboard. */
export function useDecide() {
  const qc = useQueryClient();
  const { email, role } = useReviewer();
  return useMutation({
    mutationFn: (req: Omit<DecisionRequest, "principal"> & { principal?: string }) =>
      postDecision({ role, ...req, principal: req.principal || email }),
    onMutate: (req) => {
      const id = toast.loading(LABELS[req.action]?.[0] || "Working…");
      return { id };
    },
    onSuccess: (res, req, ctx) => {
      const extra = res.status === "PARTIALLY_APPROVED" ? " — 1/2 signatures, awaiting platform sign-off" : "";
      toast.success(`${LABELS[req.action]?.[1] || "Done"}${extra}`, { id: ctx?.id });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (err: Error, _req, ctx) => {
      toast.error(err.message, { id: ctx?.id, duration: 12000 });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}
