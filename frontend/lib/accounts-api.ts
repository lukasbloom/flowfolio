import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";

export interface Account {
  id: string;
  name: string;
  account_type: string;
  is_banked: boolean;
  currency: string;
  created_at: string;
  last_reconciled_date: string | null;
}

export interface AccountInput {
  name: string;
  account_type: string;
  is_banked: boolean;
  currency: string;
}

export function useAccounts() {
  return useQuery<Account[]>({
    queryKey: ["accounts"],
    queryFn: () => apiFetch<Account[]>("/api/accounts"),
  });
}

export function useCreateAccount() {
  const qc = useQueryClient();
  return useMutation<Account, Error, AccountInput>({
    mutationFn: (input) =>
      apiFetch<Account>("/api/accounts", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
  });
}

export function useUpdateAccount() {
  const qc = useQueryClient();
  return useMutation<Account, Error, { id: string; input: AccountInput }>({
    mutationFn: ({ id, input }) =>
      apiFetch<Account>(`/api/accounts/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
  });
}
