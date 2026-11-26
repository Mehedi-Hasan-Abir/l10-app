import type { Metadata } from "next";
import { TeamPage } from "@/components/team-page";

export const metadata: Metadata = { title: "Team" };

export default async function Page({ params }: { params: Promise<{ teamId: string }> }) {
  const { teamId } = await params;
  return <TeamPage teamId={teamId} />;
}
