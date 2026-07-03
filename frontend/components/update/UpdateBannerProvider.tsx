"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useState, type ReactNode } from "react";

import { apiFetch } from "@/lib/api-client";

interface UpdateBannerContextValue {
  /**
   * The version the user optimistically dismissed this session. Null until a
   * dismiss action fires. Unlike ConcentrationBanner's in-memory-only
   * dismissal, this is also persisted server-side (dismiss-until-next-
   * version); the optimistic flag just hides the banner instantly while the
   * PUT settles. The banner's true visibility derives from the server
   * `update_available` field plus this optimistic state.
   */
  dismissedVersion: string | null;
  dismiss: (version: string) => void;
}

const UpdateBannerContext = createContext<UpdateBannerContextValue | null>(null);

export function UpdateBannerProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (version: string) =>
      apiFetch("/api/update/dismiss", {
        method: "PUT",
        body: JSON.stringify({ version }),
      }),
    onSettled: () => {
      // Reconcile with server truth once the dismissal persists (or fails).
      queryClient.invalidateQueries({ queryKey: ["update-status"] });
    },
  });

  const dismiss = (version: string) => {
    setDismissedVersion(version); // optimistic hide
    mutation.mutate(version);
  };

  return (
    <UpdateBannerContext.Provider value={{ dismissedVersion, dismiss }}>
      {children}
    </UpdateBannerContext.Provider>
  );
}

export function useUpdateBanner() {
  const ctx = useContext(UpdateBannerContext);
  if (!ctx) {
    throw new Error(
      "useUpdateBanner must be used inside UpdateBannerProvider",
    );
  }
  return ctx;
}
