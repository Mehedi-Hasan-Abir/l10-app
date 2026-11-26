"use client";

import { createContext, useContext, type ReactNode } from "react";
import { useTeamChannel, type ChannelStatus } from "./use-team-channel";

const ChannelStatusContext = createContext<ChannelStatus>("connecting");

/** Opens the team socket once and shares its connection status with the header indicator. */
export function TeamChannelProvider({ teamId, children }: { teamId: string; children: ReactNode }) {
  const status = useTeamChannel(teamId);
  return <ChannelStatusContext.Provider value={status}>{children}</ChannelStatusContext.Provider>;
}

export function useChannelStatus(): ChannelStatus {
  return useContext(ChannelStatusContext);
}
