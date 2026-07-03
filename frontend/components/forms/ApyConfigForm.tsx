"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { apiFetch } from "@/lib/api-client";
import { decimalField } from "@/lib/decimal-strings";

interface Account {
  id: string;
  name: string;
}

// Boundary mirrors backend Pydantic ApyConfigCreate.apy_positive: rejects apy_rate > 1.0
// (i.e. > 100%). Entering exactly 100 yields apy_rate = 1.0, which the backend accepts.
const schema = z.object({
  account_id: z.string().min(1, "Select an account"),
  apy_rate_pct: decimalField({ message: "Enter a positive percentage, e.g. 2.37" }).refine(
    (s) => Number(s) > 0 && Number(s) <= 100,
    "Between 0 (exclusive) and 100 (inclusive)",
  ),
  effective_from: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "YYYY-MM-DD"),
});

type FormValues = z.infer<typeof schema>;

interface ApyConfigFormProps {
  instrumentId: string;
  /**
   * When provided, the Account Select defaults to this account on mount and on
   * reset-after-save. Used by ApyConfigTab to honor the `?account=<id>` deep-link
   * from EditTxnDialog's auto-accrual ActionBanner.
   */
  accountId?: string;
}
export function ApyConfigForm({ instrumentId, accountId }: ApyConfigFormProps) {
  const qc = useQueryClient();
  const today = new Date().toISOString().slice(0, 10);

  const { data: accounts = [] } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => apiFetch<Account[]>("/api/accounts"),
  });

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      account_id: accountId ?? "",   // CHANGED — pre-select deep-linked account when provided
      apy_rate_pct: "",
      effective_from: today,
    },
  });

  const mutation = useMutation({
    mutationFn: (values: FormValues) =>
      apiFetch("/api/apy-config", {
        method: "POST",
        body: JSON.stringify({
          account_id: values.account_id,
          instrument_id: instrumentId,
          // JS Number division acceptable for APY % < 100; precision loss < 1 sat/year on $100k position.
          apy_rate: (Number(values.apy_rate_pct) / 100).toString(),
          effective_from: values.effective_from,
          compounding: "daily_simple",
        }),
      }),
    onSuccess: () => {
      toast.success("APY rate saved.");
      qc.invalidateQueries({ queryKey: ["apy-config", instrumentId] });
      form.reset({
        account_id: accountId ?? "",   // CHANGED — preserve deep-linked account across resets
        apy_rate_pct: "",
        effective_from: today,
      });
    },
    onError: (err: Error) => {
      // Surface backend Pydantic 422 detail (form-level error) — covers off-by-boundary
      // cases the zod refinement let through (e.g. apy_rate_pct values that round differently).
      const msg = err.message ?? "Unknown error";
      form.setError("apy_rate_pct", { type: "server", message: msg });
      toast.error(`Could not save APY rate. ${msg}`);
    },
  });

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit((v) => mutation.mutate(v))}
        className="grid grid-cols-1 gap-4 md:grid-cols-3 md:items-start"
      >
        <FormField
          control={form.control}
          name="account_id"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Account</FormLabel>
              <Select onValueChange={field.onChange} value={field.value}>
                <FormControl>
                  <SelectTrigger ref={field.ref}>
                    <SelectValue placeholder="Select account" />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {accounts.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="apy_rate_pct"
          render={({ field }) => (
            <FormItem>
              <FormLabel>APY (%)</FormLabel>
              <FormControl>
                <Input inputMode="decimal" placeholder="2.37" className="tabular-nums" {...field} />
              </FormControl>
              <p className="text-xs text-muted-foreground">
                Enter as percentage, e.g. 4.5 for 4.5% APY (max 100).
              </p>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="effective_from"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Effective from</FormLabel>
              <FormControl>
                <Input type="date" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="md:col-span-3">
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "Saving..." : "Save APY rate"}
          </Button>
        </div>
      </form>
    </Form>
  );
}
