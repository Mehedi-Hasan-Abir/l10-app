"use client";

import { useRouter } from "next/navigation";
import { isApiError } from "@/lib/api/client";
import { useStartMeeting } from "@/lib/api/mutations";

/**
 * Start a meeting and land on its page. If someone else won the race (409 meeting_already_active),
 * go to the meeting that exists instead of showing a dead end.
 */
export function useStartAndOpenMeeting(teamId: string) {
  const router = useRouter();
  const mutation = useStartMeeting(teamId);

  function start() {
    mutation.mutate(undefined, {
      onSettled: (_meeting, error) => {
        if (!error || (isApiError(error) && error.code === "meeting_already_active")) {
          router.push(`/teams/${teamId}/meeting`);
        }
      },
    });
  }

  return { start, isPending: mutation.isPending };
}
