"use client";

import { CircleAlert, Gauge, History, ListChecks, Users, type LucideIcon } from "lucide-react";
import { describeAuditEvent } from "@/components/audit/audit-sentence";
import { InlineError } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useAudit } from "@/lib/api/queries";
import type { AuditEvent, UserRef } from "@/lib/api/types";
import { formatDateTime } from "@/lib/time";

const ENTITY_ICONS: Record<AuditEvent["entity_type"], LucideIcon> = {
  score: Gauge,
  issue: CircleAlert,
  todo: ListChecks,
  meeting: Users,
};

/**
 * The team's audit trail, newest first: the last 20 changes, 20 more per "Load more". It stays current
 * through the team channel, which invalidates the audit query on every data change and meeting step.
 * `people` resolves user ids that appear inside event data (the owner of a new To-Do) to names.
 */
export function ActivityPanel({ teamId, people }: { teamId: string; people: readonly UserRef[] }) {
  const audit = useAudit(teamId);
  const names = new Map(people.map((person) => [person.id, person.name]));
  const events = audit.data?.pages.flatMap((page) => page.events) ?? [];

  return (
    <Card id="activity" className="scroll-mt-20">
      <CardHeader>
        <CardTitle>Activity</CardTitle>
        <CardDescription>Who changed what, newest first. Times are Central.</CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        {audit.isPending ? (
          <div className="flex flex-col gap-2 px-4" aria-busy="true" aria-label="Loading activity">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : audit.data === undefined ? (
          <div className="px-4">
            <InlineError error={audit.error} onRetry={() => void audit.refetch()} />
          </div>
        ) : events.length === 0 ? (
          <div className="px-4">
            <Empty className="border">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <History aria-hidden />
                </EmptyMedia>
                <EmptyTitle>No activity yet</EmptyTitle>
                <EmptyDescription>
                  Score edits, issues, To-Dos and meeting steps are recorded here as they happen.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          </div>
        ) : (
          <>
            <ol aria-label="Recent activity" className="flex max-h-[26rem] flex-col divide-y overflow-y-auto border-t">
              {events.map((event) => (
                <ActivityRow key={event.id} event={event} names={names} />
              ))}
            </ol>
            {audit.hasNextPage || audit.isError ? (
              <div className="flex flex-col gap-2 border-t px-4 pt-3">
                {audit.isError ? (
                  <InlineError
                    error={audit.error}
                    onRetry={() => void (audit.isFetchNextPageError ? audit.fetchNextPage() : audit.refetch())}
                  />
                ) : null}
                {audit.hasNextPage && !audit.isFetchNextPageError ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="self-center"
                    onClick={() => void audit.fetchNextPage()}
                    disabled={audit.isFetchingNextPage}
                  >
                    {audit.isFetchingNextPage ? <Spinner data-icon="inline-start" /> : null}
                    Load more
                  </Button>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function ActivityRow({ event, names }: { event: AuditEvent; names: ReadonlyMap<string, string> }) {
  const Icon = ENTITY_ICONS[event.entity_type];
  const line = describeAuditEvent(event, names);

  return (
    <li className="flex items-start gap-3 px-4 py-2.5">
      <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
        <Icon className="size-3.5" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-snug text-pretty">
          <span className="font-medium">{event.actor.name}</span> {line.text}
        </p>
        {line.detail ? <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{line.detail}</p> : null}
        <time dateTime={event.at} className="mt-0.5 block text-xs text-muted-foreground">
          {formatDateTime(event.at)}
        </time>
      </div>
    </li>
  );
}
