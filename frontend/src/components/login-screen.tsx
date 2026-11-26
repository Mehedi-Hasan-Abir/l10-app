"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Brand } from "@/components/brand";
import { InlineError } from "@/components/error-state";
import { RoleBadge } from "@/components/role-badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useLogin } from "@/lib/api/mutations";
import { useAuthUsers } from "@/lib/api/queries";
import type { UserRef } from "@/lib/api/types";
import { initials } from "@/lib/format";
import { ROLE_BLURB } from "@/lib/permissions";
import { cn } from "@/lib/utils";

/** Seeded-user switcher. There are no passwords: pick who you want to be. */
export function LoginScreen() {
  const router = useRouter();
  const users = useAuthUsers();
  const login = useLogin();
  const [pendingId, setPendingId] = useState<string | null>(null);

  function signIn(user: UserRef) {
    setPendingId(user.id);
    login.mutate(user.id, {
      onSuccess: () => router.push("/"),
      onSettled: () => setPendingId(null),
    });
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-2xl flex-col gap-8 px-4 pt-16 pb-12 sm:pt-24">
      <Brand asLink={false} />

      <div className="flex flex-col gap-2">
        <h1 className="text-3xl font-semibold">Who&apos;s running the meeting?</h1>
        <p className="text-muted-foreground text-pretty">
          Pick a user to continue. This demo has no passwords; each person sees only their own teams, and what they can
          change depends on their role.
        </p>
      </div>

      {users.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2" aria-busy="true" aria-label="Loading users">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-[4.5rem] w-full" />
          ))}
        </div>
      ) : users.isError ? (
        <InlineError error={users.error} onRetry={() => void users.refetch()} />
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {users.data.map((user) => {
            const isPending = pendingId === user.id;
            return (
              <li key={user.id}>
                <button
                  type="button"
                  onClick={() => signIn(user)}
                  disabled={pendingId !== null}
                  aria-label={`Sign in as ${user.name}, ${user.role.replace("_", " ")}`}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-xl border bg-card p-3 text-left outline-none transition-colors",
                    "hover:border-primary/40 hover:bg-accent/40 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
                    "disabled:cursor-not-allowed disabled:opacity-60",
                  )}
                >
                  <Avatar className="size-10">
                    <AvatarFallback className="bg-accent font-semibold text-accent-foreground">
                      {initials(user.name)}
                    </AvatarFallback>
                  </Avatar>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{user.name}</span>
                    <span className="mt-0.5 block truncate text-xs text-muted-foreground">{ROLE_BLURB[user.role]}</span>
                    <span className="mt-1 block truncate text-xs text-muted-foreground/80">
                      {user.teams.map((t) => t.name).join(" · ")}
                    </span>
                  </span>
                  {isPending ? <Spinner /> : <RoleBadge role={user.role} />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
