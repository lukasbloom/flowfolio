"use client";

import { useEffect } from "react";
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
import { apiFetch } from "@/lib/api-client";

interface SettingsResponse {
  settings: {
    concentration_threshold: string;
  };
}

interface MuteRow {
  instrument_id: string;
  instrument_symbol: string;
  instrument_name: string;
}

const schema = z.object({
  threshold: z
    .number({ error: "Must be a number" })
    .int("Must be a whole number")
    .min(1, "Must be at least 1")
    .max(99, "Must be at most 99"),
});

type FormValues = z.infer<typeof schema>;

export function ConcentrationSettings() {
  const qc = useQueryClient();

  const { data: settingsData } = useQuery<SettingsResponse>({
    queryKey: ["settings"],
    queryFn: () => apiFetch<SettingsResponse>("/api/settings"),
  });

  const { data: mutesData } = useQuery<MuteRow[]>({
    queryKey: ["concentration-mutes"],
    queryFn: () => apiFetch<MuteRow[]>("/api/concentration/mutes"),
  });

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { threshold: 25 },
  });

  // Populate form once settings load
  useEffect(() => {
    if (settingsData?.settings.concentration_threshold) {
      const pct = Math.round(
        Number(settingsData.settings.concentration_threshold) * 100
      );
      form.reset({ threshold: pct });
    }
  }, [settingsData, form]);

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      return apiFetch("/api/settings/concentration_threshold", {
        method: "PUT",
        body: JSON.stringify({ value: (values.threshold / 100).toString() }),
      });
    },
    onSuccess: (_data, variables) => {
      toast.success(`Threshold updated to ${variables.threshold}%.`, { duration: 3000 });
      qc.invalidateQueries({ queryKey: ["concentration"] });
      qc.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (err: Error) => {
      toast.error(`Could not save threshold. ${err.message}.`, { duration: 6000 });
    },
  });

  const muteCount = mutesData?.length ?? 0;

  return (
    <div>
      <h2 className="text-xl font-semibold">Concentration alert</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        {"We'll warn you on the dashboard when any holding exceeds this share of your net worth."}
      </p>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit((v) => mutation.mutate(v))}
          className="mt-4 flex items-end gap-3"
        >
          <FormField
            control={form.control}
            name="threshold"
            render={({ field }) => (
              <FormItem className="flex-1 max-w-[160px]">
                <FormLabel>Threshold (%)</FormLabel>
                <FormControl>
                  <Input
                    type="number"
                    min={1}
                    max={99}
                    step={1}
                    {...field}
                    onChange={(e) => field.onChange(e.target.valueAsNumber)}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button type="submit" disabled={mutation.isPending}>
            Save threshold
          </Button>
        </form>
      </Form>
      {muteCount > 0 && (
        <p className="mt-3 text-sm text-muted-foreground">
          Currently silencing alerts for {muteCount} {muteCount === 1 ? "holding" : "holdings"} — see Muted holdings below.
        </p>
      )}
    </div>
  );
}
