"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import { Link2, Plus } from "lucide-react";
import { toast } from "sonner";
import { Spinner } from "@/components/ui/spinner";
import { STATUS_META } from "@/components/status-badge";
import type { Measurable, ScoreCell as ScoreCellData } from "@/lib/api/types";
import { formatScore } from "@/lib/format";
import { formatDateLong, formatWeekLabel } from "@/lib/time";
import { cn } from "@/lib/utils";

const ISSUE_CHIP =
  "inline-flex items-center gap-1 rounded-full border border-current/30 bg-card/70 px-1.5 text-[11px] leading-4 font-medium outline-none hover:bg-card focus-visible:ring-3 focus-visible:ring-ring/50";

type ParsedScore = { ok: true; value: number | null } | { ok: false };

/** Empty clears the score; otherwise a plain decimal number below the server's 1e12 limit. */
function parseScore(text: string): ParsedScore {
  const trimmed = text.trim();
  if (trimmed === "") return { ok: true, value: null };
  if (!/^-?(\d+\.?\d*|\.\d+)$/.test(trimmed)) return { ok: false };
  const value = Number(trimmed);
  if (!Number.isFinite(value) || Math.abs(value) >= 1e12) return { ok: false };
  return { ok: true, value };
}

export interface ScoreCellProps {
  measurable: Measurable;
  cell: ScoreCellData;
  canWrite: boolean;
  /** The week under review gets a wider, more explicit cell. */
  isReviewWeek: boolean;
  makingIssue: boolean;
  onSave: (measurable: Measurable, cell: ScoreCellData, value: number | null) => void;
  onMakeIssue: (measurable: Measurable, cell: ScoreCellData) => void;
  /** Omit when the Issues panel isn't on screen (the Scorecard section of a meeting). */
  onViewIssue?: (issueId: string) => void;
}

export function ScoreCell({
  measurable,
  cell,
  canWrite,
  isReviewWeek,
  makingIssue,
  onSave,
  onMakeIssue,
  onViewIssue,
}: ScoreCellProps) {
  const { label, Icon, tone } = STATUS_META[cell.status];
  const weekLabel = formatWeekLabel(cell.week_start);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [invalid, setInvalid] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  // Enter/Escape and the blur they cause both want to finish the edit; only the first may.
  const finished = useRef(false);

  const valueText = formatScore(cell.value);
  const accessibleName = `${measurable.name}, week of ${weekLabel}: ${
    cell.value === null ? "no score" : valueText
  }, ${label}`;

  function startEdit() {
    finished.current = false;
    setInvalid(false);
    setDraft(cell.value === null ? "" : String(cell.value));
    setEditing(true);
  }

  function finish(mode: "save" | "cancel", via: "keyboard" | "blur") {
    if (finished.current) return;

    if (mode === "save") {
      const parsed = parseScore(draft);
      if (!parsed.ok) {
        if (via === "keyboard") {
          setInvalid(true);
          toast.error("Scores must be numbers", { description: "Use digits and an optional decimal point." });
          return; // keep the editor open so the typo can be fixed
        }
        toast.error("Score not saved", { description: "Scores must be numbers." });
      } else if (parsed.value !== cell.value) {
        onSave(measurable, cell, parsed.value);
      }
    }

    finished.current = true;
    setEditing(false);
    // Keyboard users keep their place in the grid.
    if (via === "keyboard") requestAnimationFrame(() => buttonRef.current?.focus());
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      finish("save", "keyboard");
    } else if (event.key === "Escape") {
      event.preventDefault();
      finish("cancel", "keyboard");
    }
  }

  const display = (
    <span className={cn("flex items-center justify-center gap-1", isReviewWeek ? "text-base" : "text-sm")}>
      <Icon className={isReviewWeek ? "size-4" : "size-3.5"} aria-hidden />
      <span className="font-mono font-medium tabular-nums">{valueText}</span>
      {isReviewWeek ? <span className="text-xs font-medium">{label}</span> : null}
    </span>
  );

  const showIssueAction = cell.status === "off_track" && (canWrite || cell.open_issue_id !== null);

  return (
    <div
      className={cn(
        "relative flex min-h-12 flex-col items-center justify-center gap-0.5 rounded-md px-1 py-1",
        tone,
        isReviewWeek && "min-h-14 ring-1 ring-current/15",
      )}
    >
      {editing ? (
        <input
          autoFocus
          inputMode="decimal"
          aria-label={`${measurable.name}, week of ${weekLabel}. Enter a score, or leave empty to clear it.`}
          aria-invalid={invalid || undefined}
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setInvalid(false);
          }}
          onKeyDown={onKeyDown}
          onBlur={() => finish("save", "blur")}
          onFocus={(e) => e.currentTarget.select()}
          className={cn(
            "h-7 w-full min-w-0 rounded-md border bg-card px-1.5 text-center font-mono text-sm tabular-nums text-foreground outline-none",
            "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/40",
            invalid && "border-destructive ring-3 ring-destructive/20",
          )}
        />
      ) : canWrite ? (
        <button
          ref={buttonRef}
          type="button"
          onClick={startEdit}
          aria-label={`${accessibleName}. Edit score`}
          title={`${measurable.name} · ${formatDateLong(cell.week_start)} · click to edit`}
          className="w-full rounded-md px-1 outline-none hover:bg-card/60 focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          {display}
        </button>
      ) : (
        <div role="group" aria-label={accessibleName}>
          {display}
        </div>
      )}

      {showIssueAction && !editing ? (
        cell.open_issue_id !== null ? (
          onViewIssue ? (
            <button
              type="button"
              onClick={() => onViewIssue(cell.open_issue_id as string)}
              aria-label={`View the open issue for ${measurable.name}, week of ${weekLabel}`}
              className={ISSUE_CHIP}
            >
              <Link2 className="size-3" aria-hidden />
              {isReviewWeek ? "View issue" : "Issue"}
            </button>
          ) : (
            <span className={cn(ISSUE_CHIP, "pointer-events-none")}>
              <Link2 className="size-3" aria-hidden />
              Issue open
            </span>
          )
        ) : (
          <button
            type="button"
            disabled={makingIssue}
            onClick={() => onMakeIssue(measurable, cell)}
            aria-label={`Make it an Issue: ${measurable.name}, week of ${weekLabel}`}
            className={cn(ISSUE_CHIP, "disabled:opacity-60")}
          >
            {makingIssue ? <Spinner className="size-3" /> : <Plus className="size-3" aria-hidden />}
            {isReviewWeek ? "Make it an Issue" : "Issue"}
          </button>
        )
      ) : null}
    </div>
  );
}
