import type { Metadata } from "next";
import { MeetingPage } from "@/components/meeting/meeting-page";

export const metadata: Metadata = { title: "Meeting" };

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ teamId: string }>;
  searchParams: Promise<{ m?: string | string[] }>;
}) {
  const { teamId } = await params;
  const { m } = await searchParams;
  // `?m=<meeting id>` opens a specific meeting, e.g. the summary of the one that just ended.
  const meetingIdParam = typeof m === "string" && m !== "" ? m : null;
  return <MeetingPage teamId={teamId} meetingIdParam={meetingIdParam} />;
}
