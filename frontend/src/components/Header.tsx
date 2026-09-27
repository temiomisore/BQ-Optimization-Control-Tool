import { useState } from "react";
import { Moon, Sparkles, Sun, UserRound, BarChart3, ExternalLink } from "lucide-react";
import type { Dashboard } from "@/lib/api";
import { useReviewer } from "@/lib/store";
import { Button } from "./ui";
import { cn } from "@/lib/utils";

export function Header({
  data,
  onCopilot,
  showAnalytics,
  onToggleAnalytics,
}: {
  data?: Dashboard;
  onCopilot: () => void;
  showAnalytics: boolean;
  onToggleAnalytics: () => void;
}) {
  const { email, setReviewer } = useReviewer();
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const [custom, setCustom] = useState(false);
  const personas = data?.personas || [];

  const toggleTheme = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("bqopt-theme", next ? "dark" : "light");
    setDark(next);
  };

  return (
    <header className="sticky top-0 z-40 border-b border-zinc-200/80 bg-white/70 backdrop-blur-xl dark:border-white/5 dark:bg-ink-950/60">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-3 px-6 py-3">
        <div className="flex items-center gap-3">
          <div className="relative grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-sky-400 via-cyan-300 to-violet-500 shadow-glow">
            <span className="text-sm font-black text-zinc-950">BQ</span>
          </div>
          <div className="leading-tight">
            <div className="text-[15px] font-semibold tracking-tight">
              Optimization <span className="gradient-text">Control Plane</span>
            </div>
            <div className="flex items-center gap-1.5 text-[11px] text-zinc-500">
              <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-emerald-400" />
              {data ? `${data.kpis.project_id} · ${data.kpis.location}` : "connecting…"}
            </div>
          </div>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-white px-2 py-1 dark:border-ink-800 dark:bg-ink-900">
            <UserRound className="h-4 w-4 text-zinc-400" />
            {custom ? (
              <input
                autoFocus
                className="w-56 bg-transparent text-xs outline-none"
                placeholder="reviewer@company.com"
                defaultValue={email}
                onBlur={(e) => {
                  if (e.target.value) setReviewer(e.target.value, "approver");
                  setCustom(false);
                }}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
              />
            ) : (
              <select
                className="max-w-[260px] cursor-pointer bg-transparent text-xs outline-none"
                value={personas.some((p) => p.email === email) ? email : "__current__"}
                onChange={(e) => {
                  if (e.target.value === "__custom__") return setCustom(true);
                  const p = personas.find((x) => x.email === e.target.value);
                  if (p) setReviewer(p.email, p.role);
                }}
                title="Reviewer identity recorded on approvals"
              >
                {!personas.some((p) => p.email === email) && <option value="__current__">{email}</option>}
                {personas.map((p) => (
                  <option key={p.email} value={p.email}>
                    {p.email} — {p.label}
                  </option>
                ))}
                <option value="__custom__">Custom reviewer…</option>
              </select>
            )}
          </div>

          <Button
            variant="ghost"
            size="sm"
            onClick={onToggleAnalytics}
            className={cn(showAnalytics && "bg-zinc-100 dark:bg-white/5")}
            title="Toggle analytics"
          >
            <BarChart3 className="h-4 w-4" /> Analytics
          </Button>
          <a href="/classic" className="hidden md:block" title="Open the classic UI">
            <Button variant="ghost" size="sm">
              <ExternalLink className="h-4 w-4" /> Classic
            </Button>
          </a>
          <Button variant="primary" size="sm" onClick={onCopilot}>
            <Sparkles className="h-4 w-4" /> Ask FinOps AI
          </Button>
          <Button variant="ghost" size="sm" onClick={toggleTheme} aria-label="Toggle theme" className="px-2">
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </header>
  );
}
