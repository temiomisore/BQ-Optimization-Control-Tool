import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Loader2, X } from "lucide-react";
import { AnimatePresence, motion, animate } from "framer-motion";
import { cn } from "@/lib/utils";

/* ----------------------------------------------------------------- Button */
type Variant = "primary" | "secondary" | "danger" | "ghost" | "success";
const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-gradient-to-r from-sky-500 to-cyan-400 text-zinc-950 hover:from-sky-400 hover:to-cyan-300 shadow-glow",
  success: "bg-emerald-500 text-zinc-950 hover:bg-emerald-400 shadow-glow-emerald",
  danger: "bg-rose-600 text-white hover:bg-rose-500",
  secondary:
    "bg-white text-zinc-700 border border-zinc-200 hover:bg-zinc-50 dark:bg-ink-850 dark:text-zinc-200 dark:border-ink-800 dark:hover:bg-zinc-800",
  ghost: "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-white/5",
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "sm" | "md";
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "secondary", size = "md", loading, className, children, disabled, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-all duration-200",
        "disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/50",
        size === "sm" ? "px-2.5 py-1.5 text-xs" : "px-4 py-2 text-sm",
        VARIANTS[variant],
        className,
      )}
      {...props}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  ),
);
Button.displayName = "Button";

/* ------------------------------------------------------------------ Badge */
export function Badge({ className, children, title }: { className?: string; children: React.ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        "bg-zinc-100 text-zinc-600 ring-zinc-200 dark:bg-white/5 dark:text-zinc-300 dark:ring-white/10",
        className,
      )}
    >
      {children}
    </span>
  );
}

/* ---------------------------------------------------------- Progress bar */
export function Meter({ value, className }: { value: number; className?: string }) {
  const v = Math.max(0, Math.min(100, value));
  const color = v >= 80 ? "from-emerald-400 to-cyan-400" : v >= 50 ? "from-amber-400 to-orange-400" : "from-rose-500 to-rose-400";
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800", className)}>
      <motion.div
        initial={{ width: 0 }}
        animate={{ width: `${v}%` }}
        transition={{ duration: 0.8, ease: "easeOut" }}
        className={cn("h-full rounded-full bg-gradient-to-r", color)}
      />
    </div>
  );
}

/* ------------------------------------------------------- Animated number */
export function AnimatedNumber({ value, format }: { value: number; format: (n: number) => string }) {
  const ref = React.useRef<HTMLSpanElement>(null);
  const prev = React.useRef(0);
  React.useEffect(() => {
    const controls = animate(prev.current, value, {
      duration: 0.9,
      ease: "easeOut",
      onUpdate: (v) => {
        if (ref.current) ref.current.textContent = format(v);
      },
    });
    prev.current = value;
    return () => controls.stop();
  }, [value, format]);
  return <span ref={ref}>{format(value)}</span>;
}

/* --------------------------------------------------------------- Skeleton */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-zinc-200 dark:bg-zinc-800/70", className)} />;
}

/* ---------------------------------------------------------------- Dialog */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  className,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <DialogPrimitive.Portal forceMount>
            <DialogPrimitive.Overlay asChild>
              <motion.div
                className="fixed inset-0 z-50 bg-zinc-950/60 backdrop-blur-sm"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
              />
            </DialogPrimitive.Overlay>
            <DialogPrimitive.Content asChild>
              <motion.div
                initial={{ opacity: 0, y: 16, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 8, scale: 0.98 }}
                transition={{ duration: 0.18 }}
                className={cn(
                  "fixed left-1/2 top-1/2 z-50 w-[92vw] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-xl border border-zinc-200 bg-white p-6 shadow-2xl dark:border-ink-800 dark:bg-ink-900",
                  className,
                )}
              >
                <div className="mb-4 flex items-start justify-between gap-4">
                  <div>
                    <DialogPrimitive.Title className="text-base font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
                      {title}
                    </DialogPrimitive.Title>
                    {description && (
                      <DialogPrimitive.Description className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
                        {description}
                      </DialogPrimitive.Description>
                    )}
                  </div>
                  <DialogPrimitive.Close className="rounded-md p-1 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-white/5">
                    <X className="h-4 w-4" />
                  </DialogPrimitive.Close>
                </div>
                {children}
              </motion.div>
            </DialogPrimitive.Content>
          </DialogPrimitive.Portal>
        )}
      </AnimatePresence>
    </DialogPrimitive.Root>
  );
}

/* ---------------------------------------------------------------- Select */
export function NativeSelect({
  value,
  onChange,
  children,
  className,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  children: React.ReactNode;
  className?: string;
  label?: string;
}) {
  return (
    <label className={cn("flex flex-col gap-1", className)}>
      {label && <span className="label">{label}</span>}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="input relative z-30 cursor-pointer py-1.5 pr-8 text-xs"
      >
        {children}
      </select>
    </label>
  );
}

/* ----------------------------------------------------------- Empty state */
export function Empty({ icon, title, hint }: { icon: React.ReactNode; title: string; hint?: string }) {
  return (
    <div className="panel flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
      <div className="rounded-full bg-zinc-100 p-3 text-zinc-500 dark:bg-white/5">{icon}</div>
      <div className="font-medium text-zinc-800 dark:text-zinc-200">{title}</div>
      {hint && <div className="max-w-md text-sm text-zinc-500">{hint}</div>}
    </div>
  );
}
