"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import { apiFetch } from "@/lib/api-client";
import { decimalField } from "@/lib/decimal-strings";
import { invalidatePortfolioCache } from "@/lib/invalidate-cache";

const schema = z.object({
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "YYYY-MM-DD"),
  price: decimalField({ positive: true, message: "Use a positive number, e.g. 13.00" }),
  note: z.string().max(200).optional(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  instrumentId: string;
  instrumentName: string;
  baseCurrency: "EUR" | "USD";
}

export function ManualNavForm({ instrumentId, instrumentName, baseCurrency }: Props) {
  const qc = useQueryClient();
  const today = new Date().toISOString().slice(0, 10);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { date: today, price: "", note: "" },
  });

  const mutation = useMutation({
    mutationFn: async (values: FormValues) =>
      apiFetch("/api/prices/manual", {
        method: "POST",
        body: JSON.stringify({
          instrument_id: instrumentId,
          date: values.date,
          price: values.price,
          currency: baseCurrency,
          note: values.note || null,
        }),
      }),
    onSuccess: (_data, values) => {
      toast.success(`NAV override saved for ${instrumentName} on ${values.date}.`);
      // Instrument-scoped NAV history (not part of the portfolio superset).
      qc.invalidateQueries({ queryKey: ["nav-history", instrumentId] });
      invalidatePortfolioCache(qc);
      form.reset({ date: today, price: "", note: "" });
    },
    onError: (err: Error) => {
      toast.error(`Could not save NAV override. ${err.message}`);
    },
  });

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
        className="grid grid-cols-1 gap-4 md:grid-cols-2"
      >
        <FormField
          control={form.control}
          name="date"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Date</FormLabel>
              <FormControl>
                <Input type="date" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="price"
          render={({ field }) => (
            <FormItem>
              <FormLabel>NAV ({baseCurrency})</FormLabel>
              <FormControl>
                <Input inputMode="decimal" placeholder="13.00" className="tabular-nums" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="note"
          render={({ field }) => (
            <FormItem className="md:col-span-2">
              <FormLabel>Note (optional)</FormLabel>
              <FormControl>
                <Input maxLength={200} {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="md:col-span-2">
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "Saving..." : "Save NAV override"}
          </Button>
        </div>
      </form>
    </Form>
  );
}
