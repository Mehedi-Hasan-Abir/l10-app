import type { ReactNode } from "react";
import { TeamShell } from "@/components/team-shell";

export default async function TeamLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ teamId: string }>;
}) {
  const { teamId } = await params;
  return <TeamShell teamId={teamId}>{children}</TeamShell>;
}
