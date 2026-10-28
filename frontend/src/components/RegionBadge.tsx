import { BellRing, Home, MapPin } from "lucide-react";
import type { ChangeSet, RegionsInfo } from "@/lib/api";
import { cn, regionName, regionOf } from "@/lib/utils";
import { Badge } from "./ui";

/** Which BigQuery region a recommendation comes from (change_sets.target_region).
 *  Colors match the "Watching N regions" strip: emerald = home region (where optimizer_ops
 *  lives), sky = any other region, amber = a region detected in the last few days. */
export function RegionBadge({
  c,
  info,
  withName = false,
}: {
  c: Pick<ChangeSet, "target_region">;
  info?: RegionsInfo;
  /** Also show the plain-English name, e.g. "us-central1 · Iowa". */
  withName?: boolean;
}) {
  const region = regionOf(c);
  if (!region) return null;
  const isHome = !!info?.home && region === info.home.toLowerCase();
  const isNew = !!info?.alerts?.some((a) => a.region === region);
  const name = regionName(region);
  const Icon = isNew ? BellRing : isHome ? Home : MapPin;
  return (
    <Badge
      className={cn(
        "font-mono",
        isNew
          ? "bg-amber-500/10 text-amber-700 ring-amber-500/30 dark:text-amber-300"
          : isHome
            ? "bg-emerald-500/10 text-emerald-700 ring-emerald-500/30 dark:text-emerald-300"
            : "bg-sky-500/10 text-sky-700 ring-sky-500/30 dark:text-sky-300",
      )}
      title={
        `Region: ${region}${name ? ` (${name})` : ""}. Where this recommendation's table or queries live.` +
        (isHome ? " Home region: optimizer_ops lives here." : "") +
        (isNew ? " This region was detected recently." : "")
      }
    >
      <Icon className="h-3 w-3" />
      {region}
      {withName && name && <span className="font-sans opacity-70">· {name}</span>}
    </Badge>
  );
}
