"use client";

import { format, parseISO } from "date-fns";
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

/** Summary payload the bulk dialog renders. Sourced from
 *  `GET /api/instruments/backfill-preview`. Colocated here so both
 *  BackfillAllButton and this dialog import a single type. */
export interface BackfillPreview {
  eligible_count: number;
  synthetic_count: number;
  earliest_first_txn_date: string | null;
  estimated_api_calls: number;
}

type SingleProps = {
  mode: "single";
  open: boolean;
  onOpenChange: (next: boolean) => void;
  onConfirm: () => void;
  isPending: boolean;
  symbol: string;
  earliestFirstTxnDate: string | null;
  estimatedApiCalls: number;
};

type BulkProps = {
  mode: "bulk";
  open: boolean;
  onOpenChange: (next: boolean) => void;
  onConfirm: () => void;
  isPending: boolean;
  preview: BackfillPreview | null;
  isLoadingPreview: boolean;
  isErrorPreview: boolean;
};

type Props = SingleProps | BulkProps;

function formatDateRange(earliest: string | null): string {
  if (earliest === null) {
    return "From your first transaction through today";
  }
  // ISO 'YYYY-MM-DD' from the API; parseISO + format keeps the locale
  // consistent with the en-GB convention used across the app.
  return `From ${format(parseISO(earliest), "d MMM yyyy")} through today`;
}

export function BackfillConfirmDialog(props: Props) {
  const isBulkLoading = props.mode === "bulk" && props.isLoadingPreview;
  const isBulkError = props.mode === "bulk" && props.isErrorPreview;
  const actionDisabled = props.isPending || isBulkLoading || isBulkError;

  return (
    <AlertDialog open={props.open} onOpenChange={props.onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Backfill price history?</AlertDialogTitle>
          <AlertDialogDescription>
            {props.mode === "single"
              ? `This fetches daily price history for ${props.symbol} from your stock/crypto price provider and stores it locally. No data is sent anywhere else.`
              : "This fetches daily price history for every eligible instrument from your stock/crypto price providers and stores it locally."}
          </AlertDialogDescription>
        </AlertDialogHeader>

        {props.mode === "single" ? (
          <SingleDetail
            symbol={props.symbol}
            earliestFirstTxnDate={props.earliestFirstTxnDate}
            estimatedApiCalls={props.estimatedApiCalls}
          />
        ) : (
          <BulkDetail
            preview={props.preview}
            isLoading={props.isLoadingPreview}
            isError={props.isErrorPreview}
          />
        )}

        <p className="text-sm text-muted-foreground">
          This can take up to 30 seconds.
        </p>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={props.isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              // Default behaviour of AlertDialogAction is to close the dialog
              // before the onClick handler resolves — that's fine for the
              // single-shot mutations we wire up, the parent toggles open=false.
              e.preventDefault();
              props.onConfirm();
            }}
            disabled={actionDisabled}
          >
            {props.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : null}
            <span className={props.isPending ? "ml-1.5" : undefined}>
              Backfill now
            </span>
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function SingleDetail({
  symbol,
  earliestFirstTxnDate,
  estimatedApiCalls,
}: {
  symbol: string;
  earliestFirstTxnDate: string | null;
  estimatedApiCalls: number;
}) {
  const callsLabel = `${estimatedApiCalls} API call${estimatedApiCalls === 1 ? "" : "s"}`;
  return (
    <ul className="text-sm text-muted-foreground space-y-1.5 list-disc pl-5">
      <li>
        <span className="font-medium text-foreground">Date range:</span>{" "}
        {formatDateRange(earliestFirstTxnDate)}
      </li>
      <li>
        <span className="font-medium text-foreground">Affects:</span>{" "}
        1 instrument — {symbol}
      </li>
      <li>
        <span className="font-medium text-foreground">External calls:</span>{" "}
        ~{callsLabel}. Well within free-tier limits.
      </li>
    </ul>
  );
}

function BulkDetail({
  preview,
  isLoading,
  isError,
}: {
  preview: BackfillPreview | null;
  isLoading: boolean;
  isError: boolean;
}) {
  if (isError) {
    return (
      <p className="text-sm text-destructive">
        Could not load preview — refresh the page and try again.
      </p>
    );
  }
  if (isLoading || preview === null) {
    return (
      <ul className="text-sm text-muted-foreground space-y-1.5 list-disc pl-5">
        <li>Loading summary…</li>
      </ul>
    );
  }

  const callsLabel = `${preview.estimated_api_calls} API call${preview.estimated_api_calls === 1 ? "" : "s"}`;
  const eligibleLabel = `${preview.eligible_count} instrument${preview.eligible_count === 1 ? "" : "s"}`;
  const syntheticClause =
    preview.synthetic_count > 0
      ? ` — ${preview.synthetic_count} synthetic fund${preview.synthetic_count === 1 ? "" : "s"} will be skipped automatically (manual NAV entries required)`
      : "";

  return (
    <ul className="text-sm text-muted-foreground space-y-1.5 list-disc pl-5">
      <li>
        <span className="font-medium text-foreground">Date range:</span>{" "}
        {formatDateRange(preview.earliest_first_txn_date)}
      </li>
      <li>
        <span className="font-medium text-foreground">Affects:</span>{" "}
        {eligibleLabel}
        {syntheticClause}
      </li>
      <li>
        <span className="font-medium text-foreground">External calls:</span>{" "}
        ~{callsLabel}. Well within free-tier limits.
      </li>
    </ul>
  );
}
