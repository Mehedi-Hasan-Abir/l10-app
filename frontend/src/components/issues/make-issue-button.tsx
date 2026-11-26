"use client";

import { CircleAlert } from "lucide-react";
import { toast } from "sonner";
import { useIssueFocus } from "@/components/issues/issue-focus";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useMakeIssueFrom, type MakeIssueFromVariables } from "@/lib/api/mutations";

export interface MakeIssueButtonProps extends MakeIssueFromVariables {
  teamId: string;
  /** What the issue is about, for the accessible name ("Make it an Issue: Hire VP of Sales"). */
  label: string;
}

/** "Make it an Issue" for an off-track Rock or an overdue To-Do (the scorecard cell has its own). */
export function MakeIssueButton({ teamId, source, id, label }: MakeIssueButtonProps) {
  const makeIssue = useMakeIssueFrom(teamId);
  const { focusIssue } = useIssueFocus();

  function handleClick() {
    makeIssue.mutate(
      { source, id },
      {
        onSuccess: ({ issue, created }) => {
          const action = { label: "Show", onClick: () => focusIssue(issue.id) };
          if (created)
            toast.success("Issue created", {
              description: issue.title,
              action,
            });
          else
            toast.info("Already an issue", {
              description: `${issue.title} is already open.`,
              action,
            });
          focusIssue(issue.id);
        },
      },
    );
  }

  return (
    <Button
      type="button"
      size="xs"
      variant="ghost"
      className="shrink-0 text-muted-foreground hover:text-foreground"
      onClick={handleClick}
      disabled={makeIssue.isPending}
      aria-label={`Make it an Issue: ${label}`}
    >
      {makeIssue.isPending ? (
        <Spinner data-icon="inline-start" />
      ) : (
        <CircleAlert data-icon="inline-start" aria-hidden />
      )}
      Issue
    </Button>
  );
}
