"use client";

import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api-client";

// Boot flags from GET /api/config: the single frontend channel for the
// demo flag. Auth-exempt and read straight off the backend settings singleton,
// so it is safe to read before any session exists.
export interface AppConfig {
  demo: boolean;
  app_version: string;
}

// useConfig — the shared boot-flags hook. staleTime Infinity because the flags
// are frozen at boot and never change within a session, so one fetch serves the
// banner, the update-panel hide-logic, and any other demo-gated surface.
export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: () => apiFetch<AppConfig>("/api/config"),
    staleTime: Infinity,
  });
}
