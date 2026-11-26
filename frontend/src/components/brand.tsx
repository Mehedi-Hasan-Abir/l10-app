import Link from "next/link";
import { cn } from "@/lib/utils";

/** The product mark: a ruled "L10" tile, in the tabular mono that carries every number in the app. */
export function Brand({ className, asLink = true }: { className?: string; asLink?: boolean }) {
  const content = (
    <>
      <span
        aria-hidden
        className="grid size-7 place-items-center rounded-md bg-primary font-mono text-[11px] font-semibold tracking-tighter text-primary-foreground"
      >
        L10
      </span>
      <span className="text-sm font-semibold tracking-tight">Level 10</span>
    </>
  );

  const classes = cn("inline-flex items-center gap-2 rounded-md outline-none", className);
  if (!asLink) return <span className={classes}>{content}</span>;
  return (
    <Link
      href="/"
      className={cn(classes, "focus-visible:ring-3 focus-visible:ring-ring/50")}
      aria-label="Level 10 home"
    >
      {content}
    </Link>
  );
}
