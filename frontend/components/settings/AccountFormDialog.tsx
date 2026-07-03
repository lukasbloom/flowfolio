"use client";

import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useCreateAccount,
  useUpdateAccount,
  type Account,
  type AccountInput,
} from "@/lib/accounts-api";

import { AccountForm } from "./AccountForm";

type Props =
  | {
      mode: "create";
      open: boolean;
      onOpenChange: (open: boolean) => void;
    }
  | {
      mode: "edit";
      account: Account;
      open: boolean;
      onOpenChange: (open: boolean) => void;
    };

export function AccountFormDialog(props: Props) {
  const createMut = useCreateAccount();
  const updateMut = useUpdateAccount();
  const isPending = createMut.isPending || updateMut.isPending;

  const title = props.mode === "create" ? "Add account" : "Edit account";
  const description =
    props.mode === "create"
      ? "Create a new account to record transactions against."
      : "Update this account's name, type, currency, or banked flag.";

  const defaultValues: AccountInput | undefined =
    props.mode === "edit"
      ? {
          name: props.account.name,
          account_type: props.account.account_type,
          is_banked: props.account.is_banked,
          currency: props.account.currency,
        }
      : undefined;

  function handleSubmit(input: AccountInput) {
    if (props.mode === "create") {
      createMut.mutate(input, {
        onSuccess: (acct) => {
          toast.success(`Account "${acct.name}" created.`);
          props.onOpenChange(false);
        },
        onError: (err) => {
          toast.error(`Could not create account. ${err.message}`, {
            duration: 6000,
          });
        },
      });
    } else {
      updateMut.mutate(
        { id: props.account.id, input },
        {
          onSuccess: (acct) => {
            toast.success(`Account "${acct.name}" updated.`);
            props.onOpenChange(false);
          },
          onError: (err) => {
            toast.error(`Could not update account. ${err.message}`, {
              duration: 6000,
            });
          },
        },
      );
    }
  }

  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <AccountForm
          defaultValues={defaultValues}
          submitLabel={props.mode === "create" ? "Add account" : "Save changes"}
          isPending={isPending}
          onSubmit={handleSubmit}
          onCancel={() => props.onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}
