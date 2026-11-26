import { MutationCache, QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, errorMessage } from "./client";

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: {
      /** The mutation reports its own errors (or handles them silently); skip the default toast. */
      skipErrorToast?: boolean;
    };
  }
}

/** Client errors (4xx) will not fix themselves on retry; network and 5xx blips get two more tries. */
function shouldRetry(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 2;
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    mutationCache: new MutationCache({
      // Every failed write surfaces the server's own message unless the hook opted out.
      onError: (error, _variables, _onMutateResult, mutation) => {
        if (mutation.meta?.skipErrorToast) return;
        toast.error(errorMessage(error));
      },
    }),
    defaultOptions: {
      queries: {
        // Realtime invalidation keeps data fresh; this only bounds how stale a quiet tab can get.
        staleTime: 15_000,
        retry: shouldRetry,
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false },
    },
  });
}
