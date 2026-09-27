import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { cn, copy } from "@/lib/utils";

export function CopyButton({ text, label = "Copied", className }: { text: string; label?: string; className?: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button
      type="button"
      onClick={async (e) => {
        e.stopPropagation();
        if (await copy(text)) {
          setOk(true);
          toast.success(label);
          setTimeout(() => setOk(false), 1500);
        }
      }}
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-800 dark:hover:bg-white/5 dark:hover:text-zinc-200",
        className,
      )}
      title="Copy"
    >
      {ok ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

export function CodeBlock({
  code,
  title,
  tone = "emerald",
  right,
}: {
  code: string;
  title?: React.ReactNode;
  tone?: "emerald" | "rose" | "sky";
  right?: React.ReactNode;
}) {
  const color = tone === "rose" ? "text-rose-300" : tone === "sky" ? "text-sky-300" : "text-emerald-300";
  return (
    <div className="overflow-hidden rounded-lg border border-zinc-200 dark:border-ink-800">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 bg-zinc-900 px-3 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">{title}</span>
        <div className="flex items-center gap-1">
          {right}
          <CopyButton text={code} label="SQL copied" className="text-zinc-400 hover:!bg-white/10 hover:!text-zinc-100" />
        </div>
      </div>
      <pre className={cn("max-h-80 overflow-auto bg-zinc-950 p-4 font-mono text-[12px] leading-relaxed", color)}>{code}</pre>
    </div>
  );
}
