"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { AccountInput } from "@/lib/accounts-api";

export interface AccountFormProps {
  defaultValues?: AccountInput;
  submitLabel: string;
  pendingLabel?: string;
  isPending: boolean;
  onSubmit: (input: AccountInput) => void;
  onCancel: () => void;
}

const EMPTY: AccountInput = {
  name: "",
  account_type: "",
  is_banked: true,
  currency: "EUR",
};

export function AccountForm({
  defaultValues,
  submitLabel,
  pendingLabel = "Saving…",
  isPending,
  onSubmit,
  onCancel,
}: AccountFormProps) {
  const [values, setValues] = useState<AccountInput>(defaultValues ?? EMPTY);
  const [touched, setTouched] = useState<{ name: boolean; account_type: boolean }>({
    name: false,
    account_type: false,
  });

  const nameError =
    touched.name && values.name.trim() === "" ? "Name is required." : null;
  const typeError =
    touched.account_type && values.account_type.trim() === ""
      ? "Account type is required."
      : null;
  const canSubmit =
    values.name.trim() !== "" && values.account_type.trim() !== "" && !isPending;

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        setTouched({ name: true, account_type: true });
        if (!canSubmit) return;
        onSubmit({
          name: values.name.trim(),
          account_type: values.account_type.trim(),
          is_banked: values.is_banked,
          currency: values.currency,
        });
      }}
    >
      <div className="space-y-2">
        <Label htmlFor="acct-name">Name</Label>
        <Input
          id="acct-name"
          value={values.name}
          onChange={(e) => setValues((v) => ({ ...v, name: e.target.value }))}
          onBlur={() => setTouched((t) => ({ ...t, name: true }))}
          placeholder="e.g. Revolut, XTB, MyInvestor"
          autoFocus
        />
        {nameError && <p className="text-xs text-destructive">{nameError}</p>}
      </div>

      <div className="space-y-2">
        <Label htmlFor="acct-type">Account type</Label>
        <Input
          id="acct-type"
          value={values.account_type}
          onChange={(e) =>
            setValues((v) => ({ ...v, account_type: e.target.value }))
          }
          onBlur={() => setTouched((t) => ({ ...t, account_type: true }))}
          placeholder="e.g. Brokerage, Crypto Exchange, Bank, Cold Wallet"
        />
        {typeError && <p className="text-xs text-destructive">{typeError}</p>}
      </div>

      <div className="space-y-2">
        <Label htmlFor="acct-currency">Currency</Label>
        <Select
          value={values.currency}
          onValueChange={(v) => setValues((vv) => ({ ...vv, currency: v }))}
        >
          <SelectTrigger id="acct-currency">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="EUR">EUR</SelectItem>
            <SelectItem value="USD">USD</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
        <div className="space-y-0.5">
          <Label htmlFor="acct-banked" className="text-sm font-medium">
            Banked account
          </Label>
          <p className="text-xs text-muted-foreground">
            Holds cash balances (vs. crypto-only / cold-storage).
          </p>
        </div>
        <Switch
          id="acct-banked"
          checked={values.is_banked}
          onCheckedChange={(c) => setValues((v) => ({ ...v, is_banked: c }))}
        />
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
          disabled={isPending}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={!canSubmit}>
          {isPending ? pendingLabel : submitLabel}
        </Button>
      </div>
    </form>
  );
}
