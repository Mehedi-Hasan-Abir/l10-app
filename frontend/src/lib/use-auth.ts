"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { isApiError } from "@/lib/api/client";
import { useMe } from "@/lib/api/queries";

/** The signed-in user, or a redirect to /login when the session is missing or has expired. */
export function useRequireUser() {
  const router = useRouter();
  const me = useMe();
  const unauthenticated = isApiError(me.error) && me.error.status === 401;

  useEffect(() => {
    if (unauthenticated) router.replace("/login");
  }, [unauthenticated, router]);

  return me;
}
