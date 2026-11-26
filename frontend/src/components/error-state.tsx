"use client";

import Link from "next/link";
import { CloudOff, LockKeyhole, SearchX, TriangleAlert, type LucideIcon } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { isApiError } from "@/lib/api/client";

interface ErrorCopy {
  title: string;
  description: string;
  Icon: LucideIcon;
  /** Retrying can't fix a permission or a missing item, so don't offer it. */
  retryable: boolean;
}

/** Turn any thrown value into friendly, specific copy. The server's own message is the fallback. */
export function describeError(error: unknown): ErrorCopy {
  if (isApiError(error)) {
    if (error.code === "network_error" || error.code === "server_unavailable") {
      return {
        title: "Can't reach the server",
        description: "Check that the API is running and your connection is up, then retry.",
        Icon: CloudOff,
        retryable: true,
      };
    }
    if (error.status === 403) {
      return {
        title: "You don't have access to this team",
        description:
          error.code === "team_forbidden"
            ? "Your account isn't a member of this team, or the team doesn't exist."
            : error.message,
        Icon: LockKeyhole,
        retryable: false,
      };
    }
    if (error.status === 404) {
      return {
        title: "Not found",
        description: error.message,
        Icon: SearchX,
        retryable: false,
      };
    }
    return { title: "Something went wrong", description: error.message, Icon: TriangleAlert, retryable: true };
  }
  return {
    title: "Something went wrong",
    description: error instanceof Error ? error.message : "Try again in a moment.",
    Icon: TriangleAlert,
    retryable: true,
  };
}

/** Full-area error, e.g. a team the user can't open. */
export function PageError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { title, description, Icon, retryable } = describeError(error);
  return (
    <Empty className="mx-auto mt-10 max-w-lg border bg-card py-12">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Icon aria-hidden />
        </EmptyMedia>
        <EmptyTitle className="text-base">{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <div className="flex gap-2">
          {retryable && onRetry ? (
            <Button variant="outline" onClick={onRetry}>
              Retry
            </Button>
          ) : null}
          <Button asChild>
            <Link href="/">Go to my teams</Link>
          </Button>
        </div>
      </EmptyContent>
    </Empty>
  );
}

/** Error inside a panel: the rest of the page keeps working. */
export function InlineError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { title, description, retryable } = describeError(error);
  return (
    <Alert variant="destructive">
      <TriangleAlert aria-hidden />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{description}</p>
        {retryable && onRetry ? (
          <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
            Retry
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}
