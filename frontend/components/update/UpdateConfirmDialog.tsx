"use client";

import { Loader2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { withV } from "@/lib/update-status";

interface Props {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  onConfirm: () => void;
  isPending: boolean;
  latest: string;
  current: string;
  /**
   * Whether an encrypted pre-update snapshot will be taken (BACKUP_ENCRYPTION_KEY
   * is set). When false the dialog must NOT promise automatic rollback / "data
   * never lost", the snapshot is skipped and that safety net does not exist.
   */
  backupsConfigured: boolean;
}

/**
 * Confirm dialog for the one-click update. Composed exactly
 * like BackfillConfirmDialog. The action is PRIMARY, not destructive, the
 * update is recoverable (snapshot + rollback); the dialog only warns
 * about the brief restart.
 */
export function UpdateConfirmDialog({
  open,
  onOpenChange,
  onConfirm,
  isPending,
  latest,
  current,
  backupsConfigured,
}: Props) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Update to Flowfolio {withV(latest)}?</AlertDialogTitle>
          <AlertDialogDescription>
            This downloads the new version and restarts the app, so Flowfolio
            will be briefly unavailable (usually under a minute).
          </AlertDialogDescription>
        </AlertDialogHeader>

        <ul className="text-sm text-muted-foreground space-y-1.5 list-disc pl-5">
          {backupsConfigured ? (
            <>
              <li>
                <span className="font-medium text-foreground">Backup:</span>{" "}
                An encrypted snapshot is taken first.
              </li>
              <li>
                <span className="font-medium text-foreground">Restart:</span>{" "}
                The app restarts on the new version and runs any database updates.
              </li>
              <li>
                <span className="font-medium text-foreground">Safety:</span>{" "}
                If anything fails, Flowfolio rolls back to {withV(current)}{" "}
                automatically. Your data is never lost.
              </li>
            </>
          ) : (
            <>
              <li>
                <span className="font-medium text-foreground">Restart:</span>{" "}
                The app restarts on the new version and runs any database updates.
              </li>
              <li className="text-destructive">
                <span className="font-medium">No backup configured:</span>{" "}
                Set BACKUP_ENCRYPTION_KEY to enable an encrypted pre-update
                snapshot. Without it the update proceeds with no snapshot, so if
                it fails after changing the database your data cannot be
                automatically restored.
              </li>
            </>
          )}
        </ul>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              // Parent owns the open state: it closes the dialog and opens the
              // blocking overlay, so prevent the default auto-close race.
              e.preventDefault();
              onConfirm();
            }}
            disabled={isPending}
          >
            {isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : null}
            <span className={isPending ? "ml-1.5" : undefined}>Update now</span>
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
