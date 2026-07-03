"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { LastReconciledBadge } from "@/components/reconciliation/LastReconciledBadge";
import { useAccounts, type Account } from "@/lib/accounts-api";

import { AccountFormDialog } from "./AccountFormDialog";

type DialogState =
  | { mode: "closed" }
  | { mode: "create" }
  | { mode: "edit"; account: Account };

export function AccountsSection() {
  const { data: accounts, isLoading, isError, error } = useAccounts();
  const [dialogState, setDialogState] = useState<DialogState>({ mode: "closed" });

  return (
    <section aria-labelledby="accounts-section-heading" className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id="accounts-section-heading" className="text-base font-semibold">
            Accounts
          </h2>
          <p className="text-sm text-muted-foreground">
            Reconcile each account against your broker&apos;s reported quantities.
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => setDialogState({ mode: "create" })}
          className="shrink-0 min-h-11 sm:min-h-9"
        >
          Add account
        </Button>
      </div>

      {isLoading && (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      )}

      {isError && (
        <p className="text-sm text-destructive">
          Could not load accounts. {(error as Error).message}
        </p>
      )}

      {accounts && accounts.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No accounts yet. Click <span className="font-medium">Add account</span> to create your first one.
        </p>
      )}

      {accounts && accounts.length > 0 && (
        <ul className="divide-y divide-border rounded-md border border-border">
          {accounts.map((account) => (
            <li
              key={account.id}
              className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="flex items-center gap-3">
                <span className="text-sm font-medium">{account.name}</span>
                <LastReconciledBadge
                  lastReconciledDate={account.last_reconciled_date}
                />
              </div>
              <div className="flex items-center gap-2 self-start sm:self-auto">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setDialogState({ mode: "edit", account })}
                  className="min-h-11 sm:min-h-9"
                >
                  Edit
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  asChild
                  className="min-h-11 sm:min-h-9"
                >
                  <Link href={`/reconcile?account=${account.id}`}>Reconcile</Link>
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {dialogState.mode === "create" && (
        <AccountFormDialog
          mode="create"
          open
          onOpenChange={(o) => !o && setDialogState({ mode: "closed" })}
        />
      )}
      {dialogState.mode === "edit" && (
        <AccountFormDialog
          mode="edit"
          account={dialogState.account}
          open
          onOpenChange={(o) => !o && setDialogState({ mode: "closed" })}
        />
      )}
    </section>
  );
}
