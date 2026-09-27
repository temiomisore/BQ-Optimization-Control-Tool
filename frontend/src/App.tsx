import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Inbox, RefreshCw } from "lucide-react";
import { fetchDashboard, type ChangeSet, type Dashboard } from "@/lib/api";
import { ReviewerProvider } from "@/lib/store";
import { cn, fullPath, needsTwo } from "@/lib/utils";
import { Header } from "@/components/Header";
import { KpiCards, type TabKey } from "@/components/KpiCards";
import { AnalyticsStrip } from "@/components/AnalyticsStrip";
import { DEFAULT_FILTERS, Toolbar, type Filters } from "@/components/Toolbar";
import { CardRow } from "@/components/CardRow";
import { CardDetail } from "@/components/CardDetail";
import { BlockedList, ReceiptsTable, RegressedBanner, RegressedList, RolledBackList } from "@/components/OpsViews";
import { GeminiAssistDrawer } from "@/components/GeminiAssistDrawer";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { PrModal } from "@/components/PrModal";
import { Button, Empty, Skeleton } from "@/components/ui";

function applyFilters(cards: ChangeSet[], f: Filters) {
  const q = f.q.trim().toLowerCase();
  const out = cards.filter((c) => {
    if (f.cls && c.apply_class !== f.cls) return false;
    if (Number(c.net_monthly_value_usd || 0) < f.minSavings) return false;
    if (f.project && c.target_project !== f.project) return false;
    if (f.dataset && c.target_dataset !== f.dataset) return false;
    if (f.director && c.director_name !== f.director) return false;
    const conf = Number(c.confidence || 0);
    if (f.confidence === "high" && conf < 0.8) return false;
    if (f.confidence === "med" && (conf < 0.5 || conf >= 0.8)) return false;
    if (f.confidence === "twoperson" && !needsTwo(c)) return false;
    if (q) {
      const hay = [
        fullPath(c), c.change_set_id, (c.rule_ids || []).join(" "), c.finding_summary,
        c.director_name, c.department, c.evidence?.current_sql, c.evidence?.proposed_sql, c.proposed?.generated_ddl,
      ].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const val = (c: ChangeSet) => Number(c.net_monthly_value_usd || 0);
  const sorters: Record<Filters["sort"], (a: ChangeSet, b: ChangeSet) => number> = {
    savings_desc: (a, b) => val(b) - val(a),
    savings_asc: (a, b) => val(a) - val(b),
    confidence_desc: (a, b) => Number(b.confidence || 0) - Number(a.confidence || 0),
    class_asc: (a, b) => a.apply_class - b.apply_class || val(b) - val(a),
    table_asc: (a, b) => fullPath(a).localeCompare(fullPath(b)),
  };
  return out.sort(sorters[f.sort]);
}

function Shell({ data, isLoading, error, refetch, isFetching }: {
  data?: Dashboard; isLoading: boolean; error: Error | null; refetch: () => void; isFetching: boolean;
}) {
  const [tab, setTab] = useState<TabKey>("queue");
  const [f, setFState] = useState<Filters>(DEFAULT_FILTERS);
  const setF = (p: Partial<Filters>) => setFState((s) => ({ ...s, ...p }));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [assistOpen, setAssistOpen] = useState(false);
  const [analytics, setAnalytics] = useState(true);
  const [prCard, setPrCard] = useState<ChangeSet | null>(null);
  const closeAssist = useCallback(() => setAssistOpen(false), []);

  const cards = data?.cards || [];
  const shown = useMemo(() => applyFilters(cards, f), [cards, f]);
  const selected = shown.find((c) => c.change_set_id === selectedId) || cards.find((c) => c.change_set_id === selectedId) || null;

  const tabs: { key: TabKey; label: string; count: number; tone?: string }[] = [
    { key: "queue", label: "Review queue", count: cards.length },
    { key: "blocked", label: "Blocked", count: data?.blocked_cards.length || 0, tone: "text-amber-400" },
    { key: "regressed", label: "Regressed", count: data?.regressed_cards.length || 0, tone: "text-rose-400" },
    { key: "rolled_back", label: "Rolled back", count: data?.rolled_back_cards.length || 0 },
    { key: "receipts", label: "Receipts", count: data?.receipts.length || 0, tone: "text-emerald-400" },
  ];

  return (
    <div className="min-h-screen">
      <Header data={data} onAssist={() => setAssistOpen(true)} showAnalytics={analytics} onToggleAnalytics={() => setAnalytics((a) => !a)} />

      <main className="mx-auto max-w-[1600px] space-y-6 px-4 pb-16 pt-6 sm:px-6">
        {error && (
          <div className="panel flex flex-wrap items-center gap-3 border-rose-500/40 p-4">
            <AlertTriangle className="h-5 w-5 text-rose-400" />
            <div className="flex-1 text-sm">
              <div className="font-medium text-rose-300">Couldn't load the dashboard</div>
              <div className="text-xs text-zinc-500">{error.message}</div>
            </div>
            <Button variant="secondary" onClick={refetch} loading={isFetching}>
              <RefreshCw className="h-4 w-4" />Retry
            </Button>
          </div>
        )}

        {data && <RegressedBanner cards={data.regressed_cards} />}

        <KpiCards data={data} onTab={(t) => { setTab(t); setSelectedId(null); }} />

        <AnimatePresence initial={false}>
          {analytics && data && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="overflow-hidden"
            >
              <AnalyticsStrip data={data} onClass={(cls) => { setTab("queue"); setF({ cls }); }} />
            </motion.div>
          )}
        </AnimatePresence>

        {/* tabs */}
        <div className="flex flex-wrap items-center gap-1 border-b border-zinc-200 dark:border-ink-800">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => { setTab(t.key); setSelectedId(null); }}
              className={cn(
                "relative flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors",
                tab === t.key ? "text-zinc-950 dark:text-zinc-50" : "text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-300",
              )}
            >
              {t.label}
              <span className={cn("rounded-full bg-zinc-100 px-1.5 font-mono text-[10px] dark:bg-white/5", t.count ? t.tone : "")}>{t.count}</span>
              {tab === t.key && (
                <motion.span layoutId="tab-underline" className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-gradient-to-r from-sky-400 to-cyan-300" />
              )}
            </button>
          ))}
          {isFetching && !isLoading && <RefreshCw className="ml-auto mr-2 h-3.5 w-3.5 animate-spin text-zinc-500" />}
        </div>

        {isLoading && (
          <div className="grid gap-4 md:grid-cols-2">
            {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-32" />)}
          </div>
        )}

        {data && tab === "queue" && (
          <>
            <Toolbar data={data} f={f} setF={setF} shown={shown.length} total={cards.length} />
            <div className="flex items-start gap-6">
              <div className={cn("min-w-0 transition-all duration-300", selected ? "hidden lg:block lg:flex-[2] xl:flex-[5]" : "w-full")}>
                {shown.length === 0 ? (
                  <Empty icon={<Inbox className="h-5 w-5" />} title="No recommendations match" hint="Try clearing filters or lowering the minimum savings." />
                ) : (
                  <div className={cn("grid gap-3", selected ? "grid-cols-1" : "md:grid-cols-2")}>
                    {shown.map((c, i) => (
                      <CardRow
                        key={c.change_set_id}
                        c={c}
                        index={i}
                        selected={c.change_set_id === selectedId}
                        compact={!!selected}
                        onClick={() => setSelectedId(c.change_set_id === selectedId ? null : c.change_set_id)}
                      />
                    ))}
                  </div>
                )}
              </div>
              <AnimatePresence mode="wait">
                {selected && (
                  <div className="w-full min-w-0 self-start lg:sticky lg:top-20 lg:flex-[3] xl:flex-[8]">
                    <CardDetail c={selected} data={data} onClose={() => setSelectedId(null)} onPr={setPrCard} />
                  </div>
                )}
              </AnimatePresence>
            </div>
          </>
        )}

        {data && tab === "blocked" && <BlockedList cards={data.blocked_cards} data={data} />}
        {data && tab === "regressed" && <RegressedList cards={data.regressed_cards} />}
        {data && tab === "rolled_back" && <RolledBackList cards={data.rolled_back_cards} />}
        {data && tab === "receipts" && <ReceiptsTable receipts={data.receipts} />}
      </main>

      <ErrorBoundary where="assist-drawer" fallback={() => null}>
        <GeminiAssistDrawer open={assistOpen} onClose={closeAssist} />
      </ErrorBoundary>
      <PrModal c={prCard} onClose={() => setPrCard(null)} />
    </div>
  );
}

export default function App() {
  const q = useQuery({ queryKey: ["dashboard"], queryFn: fetchDashboard });
  return (
    <ReviewerProvider initial={q.data?.personas?.[0] || null}>
      <Shell
        data={q.data}
        isLoading={q.isLoading}
        error={q.error as Error | null}
        refetch={() => q.refetch()}
        isFetching={q.isFetching}
      />
    </ReviewerProvider>
  );
}
