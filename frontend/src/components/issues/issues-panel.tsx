"use client";

import { useState } from "react";
import { CircleCheckBig, Inbox } from "lucide-react";
import { InlineError } from "@/components/error-state";
import { CreateIssueForm } from "@/components/issues/create-issue-form";
import { useIssueFocus } from "@/components/issues/issue-focus";
import { IssueRow } from "@/components/issues/issue-row";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSetIssuePriority } from "@/lib/api/mutations";
import { useIssues } from "@/lib/api/queries";
import type { Issue, UserRef } from "@/lib/api/types";

type Tab = "unsolved" | "solved";

export interface IssuesPanelProps {
  teamId: string;
  canWrite: boolean;
  eligibleOwners: UserRef[];
  /** "meeting" marks the top issue as what the team is working on. */
  context?: "team" | "meeting";
}

export function IssuesPanel({ teamId, canWrite, eligibleOwners, context = "team" }: IssuesPanelProps) {
  const [tab, setTab] = useState<Tab>("unsolved");
  const { focusedIssueId, clearFocus } = useIssueFocus();
  // Pointing at an open issue (from a score cell or a toast) always lands on the Unsolved list.
  const activeTab: Tab = focusedIssueId ? "unsolved" : tab;

  const unsolved = useIssues(teamId, "unsolved");
  const solved = useIssues(teamId, "solved", activeTab === "solved");
  const setPriority = useSetIssuePriority(teamId);

  const openCount = unsolved.data?.length;

  return (
    <Card id="issues" className="scroll-mt-20">
      <Tabs
        value={activeTab}
        onValueChange={(next) => {
          clearFocus();
          setTab(next as Tab);
        }}
        className="gap-0"
      >
        <CardHeader>
          <CardTitle>Issues</CardTitle>
          <CardDescription>
            {context === "meeting"
              ? "Work the list from the top: identify, discuss, solve."
              : "Unsolved issues, highest priority first, then oldest."}
          </CardDescription>
        </CardHeader>
        <div className="px-(--card-spacing) pt-3">
          <TabsList>
            <TabsTrigger value="unsolved">Unsolved{openCount !== undefined ? ` (${openCount})` : ""}</TabsTrigger>
            <TabsTrigger value="solved">Solved</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="unsolved" className="mt-3 flex flex-col gap-3 pb-1">
          {canWrite ? (
            <div className="px-(--card-spacing)">
              <CreateIssueForm teamId={teamId} />
            </div>
          ) : null}
          <IssueList
            query={unsolved}
            teamId={teamId}
            canWrite={canWrite}
            eligibleOwners={eligibleOwners}
            upNextFirst={context === "meeting"}
            onSetPriority={(issue, priority) => setPriority.mutate({ issue, priority })}
            emptyTitle="No open issues"
            emptyDescription={
              canWrite
                ? "Add one above, or make an off-track score an issue from the Scorecard."
                : "Nothing is waiting to be solved."
            }
            emptyIcon="inbox"
          />
        </TabsContent>
        <TabsContent value="solved" className="mt-3 flex flex-col gap-3 pb-1">
          <IssueList
            query={solved}
            teamId={teamId}
            canWrite={false}
            eligibleOwners={eligibleOwners}
            upNextFirst={false}
            onSetPriority={() => undefined}
            emptyTitle="Nothing solved yet"
            emptyDescription="Solved issues appear here with when they were solved and the note."
            emptyIcon="done"
          />
        </TabsContent>
      </Tabs>
    </Card>
  );
}

interface IssueListProps {
  query: ReturnType<typeof useIssues>;
  teamId: string;
  canWrite: boolean;
  eligibleOwners: UserRef[];
  upNextFirst: boolean;
  onSetPriority: (issue: Issue, priority: number | null) => void;
  emptyTitle: string;
  emptyDescription: string;
  emptyIcon: "inbox" | "done";
}

function IssueList({
  query,
  teamId,
  canWrite,
  eligibleOwners,
  upNextFirst,
  onSetPriority,
  emptyTitle,
  emptyDescription,
  emptyIcon,
}: IssueListProps) {
  if (query.isPending) {
    return (
      <div className="flex flex-col gap-3 px-4" aria-busy="true" aria-label="Loading issues">
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="px-4">
        <InlineError error={query.error} onRetry={() => void query.refetch()} />
      </div>
    );
  }
  if (query.data.length === 0) {
    const Icon = emptyIcon === "inbox" ? Inbox : CircleCheckBig;
    return (
      <Empty className="mx-4 border">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Icon aria-hidden />
          </EmptyMedia>
          <EmptyTitle>{emptyTitle}</EmptyTitle>
          <EmptyDescription>{emptyDescription}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <>
      <Separator />
      {/* API order is the product's order (priority, then oldest); never re-sort on the client. */}
      <ol className="flex flex-col divide-y">
        {query.data.map((issue, index) => (
          <IssueRow
            key={issue.id}
            teamId={teamId}
            issue={issue}
            canWrite={canWrite}
            eligibleOwners={eligibleOwners}
            isUpNext={upNextFirst && index === 0}
            onSetPriority={onSetPriority}
          />
        ))}
      </ol>
    </>
  );
}
