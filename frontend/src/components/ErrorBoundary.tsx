import React from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

/** Report a client-side crash to Cloud Run logs so it can be diagnosed later. */
export function reportClientError(where: string, error: unknown, componentStack?: string) {
  try {
    const e = error as Error;
    const body = JSON.stringify({
      where,
      message: String(e?.message || error),
      stack: String(e?.stack || "").slice(0, 4000),
      componentStack: String(componentStack || "").slice(0, 4000),
      url: location.href,
      ua: navigator.userAgent,
    });
    navigator.sendBeacon?.("/api/client-error", new Blob([body], { type: "application/json" })) ||
      fetch("/api/client-error", { method: "POST", headers: { "Content-Type": "application/json" }, body });
  } catch {
    /* never let reporting itself crash */
  }
}

interface Props {
  where: string;
  children: React.ReactNode;
  /** Render prop for a compact, in-place fallback. */
  fallback?: (error: Error, reset: () => void) => React.ReactNode;
}

/** Stops a render crash in one part of the UI from blanking the whole page. */
export class ErrorBoundary extends React.Component<Props, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`[${this.props.where}]`, error, info.componentStack);
    reportClientError(this.props.where, error, info.componentStack || undefined);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return (
      <div className="mx-auto mt-24 max-w-lg px-6">
        <div className="panel space-y-3 p-6 text-center">
          <AlertTriangle className="mx-auto h-8 w-8 text-amber-400" />
          <div className="font-semibold">Something went wrong in this view</div>
          <div className="break-words font-mono text-xs text-zinc-500">{error.message}</div>
          <button
            onClick={() => location.reload()}
            className="inline-flex items-center gap-2 rounded-lg bg-sky-500 px-4 py-2 text-sm font-medium text-zinc-950"
          >
            <RefreshCw className="h-4 w-4" /> Reload
          </button>
        </div>
      </div>
    );
  }
}
