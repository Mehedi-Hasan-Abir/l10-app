"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ArrowLeft, Check, ChevronsUpDown, LogOut, Users } from "lucide-react";
import { Brand } from "@/components/brand";
import { ConnectionIndicator } from "@/components/connection-indicator";
import { MeetingPill } from "@/components/meeting-pill";
import { RoleBadge } from "@/components/role-badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useLogout } from "@/lib/api/mutations";
import { useMe, useTeam } from "@/lib/api/queries";
import { initials } from "@/lib/format";
import { cn } from "@/lib/utils";

export function AppHeader({ teamId }: { teamId: string }) {
  const pathname = usePathname();
  const onMeetingPage = pathname === `/teams/${teamId}/meeting`;
  const team = useTeam(teamId);

  return (
    <header className="sticky top-0 z-30 border-b bg-card/90 backdrop-blur supports-backdrop-filter:bg-card/80">
      <div className="mx-auto flex h-14 w-full max-w-[1500px] items-center gap-3 px-4 lg:px-6">
        <Brand />
        <span aria-hidden className="h-5 w-px bg-border" />
        <TeamSwitcher teamId={teamId} />

        <div className="ml-auto flex items-center gap-3">
          {team.isError ? null : <ConnectionIndicator className="hidden sm:inline-flex" />}
          {onMeetingPage ? (
            <Button asChild variant="outline" size="lg">
              <Link href={`/teams/${teamId}`}>
                <ArrowLeft data-icon="inline-start" aria-hidden />
                Team page
              </Link>
            </Button>
          ) : team.isError ? null : (
            <MeetingPill teamId={teamId} />
          )}
          <UserMenu />
        </div>
      </div>
    </header>
  );
}

function TeamSwitcher({ teamId }: { teamId: string }) {
  const me = useMe();
  const team = useTeam(teamId);
  const pathname = usePathname();
  const onMeetingPage = pathname === `/teams/${teamId}/meeting`;

  // A team the user can't open has no name to show; the menu still offers the ones they can.
  const currentName =
    me.data?.teams.find((t) => t.id === teamId)?.name ?? team.data?.name ?? (me.data ? "Choose a team" : undefined);
  if (!currentName) {
    return me.isError ? <span className="text-sm text-muted-foreground">Team</span> : <Skeleton className="h-8 w-32" />;
  }

  const teams = me.data?.teams ?? [];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="lg"
          className="gap-1.5 font-semibold"
          aria-label={`Team: ${currentName}. Switch team`}
        >
          <Users data-icon="inline-start" className="text-muted-foreground" aria-hidden />
          {currentName}
          <ChevronsUpDown data-icon="inline-end" className="text-muted-foreground" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Your teams</DropdownMenuLabel>
          {teams.map((t) => (
            <DropdownMenuItem key={t.id} asChild>
              {/* Switching teams lands on that team's page; a meeting in the old team makes no sense there. */}
              <Link href={`/teams/${t.id}`} aria-current={t.id === teamId && !onMeetingPage ? "page" : undefined}>
                <span className="flex-1">{t.name}</span>
                {t.id === teamId ? <Check className="size-4" aria-hidden /> : null}
              </Link>
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function UserMenu() {
  const router = useRouter();
  const me = useMe();
  const logout = useLogout();
  const user = me.data?.user;

  if (!user) return <Skeleton className="size-8 rounded-full" />;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="lg" className={cn("gap-2 px-1.5")} aria-label={`Account: ${user.name}`}>
          <Avatar className="size-6">
            <AvatarFallback className="bg-accent text-[10px] font-semibold text-accent-foreground">
              {initials(user.name)}
            </AvatarFallback>
          </Avatar>
          <span className="hidden max-w-32 truncate md:inline">{user.name}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="flex flex-col gap-1 py-2">
            <span className="text-sm font-medium text-foreground">{user.name}</span>
            <RoleBadge role={user.role} className="w-fit" />
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem asChild>
            <Link href="/login">Switch user</Link>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => logout.mutate(undefined, { onSettled: () => router.push("/login") })}>
            <LogOut aria-hidden />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
