"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Users } from "lucide-react";
import { Brand } from "@/components/brand";
import { PageError } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { isApiError } from "@/lib/api/client";
import { useLogout } from "@/lib/api/mutations";
import { useMe } from "@/lib/api/queries";

/** Landing route: send people to their first team, or to sign in. */
export default function Home() {
  const router = useRouter();
  const me = useMe();
  const logout = useLogout();

  const unauthenticated = isApiError(me.error) && me.error.status === 401;
  const firstTeamId = me.data?.teams[0]?.id;

  useEffect(() => {
    if (unauthenticated) router.replace("/login");
    else if (firstTeamId) router.replace(`/teams/${firstTeamId}`);
  }, [unauthenticated, firstTeamId, router]);

  return (
    <div className="mx-auto flex min-h-screen max-w-xl flex-col items-center gap-8 px-4 pt-24">
      <Brand asLink={false} />
      {me.isError && !unauthenticated ? (
        <PageError error={me.error} onRetry={() => void me.refetch()} />
      ) : me.data && !firstTeamId ? (
        <Empty className="border bg-card py-12">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Users aria-hidden />
            </EmptyMedia>
            <EmptyTitle className="text-base">You&apos;re not on a team yet</EmptyTitle>
            <EmptyDescription>
              Ask an admin to add {me.data.user.name} to a team, or sign in as someone else.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button
              variant="outline"
              onClick={() => logout.mutate(undefined, { onSettled: () => router.push("/login") })}
            >
              Switch user
            </Button>
          </EmptyContent>
        </Empty>
      ) : (
        <div className="flex w-full flex-col gap-3" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}
    </div>
  );
}
