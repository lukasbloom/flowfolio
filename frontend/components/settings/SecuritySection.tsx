"use client";

import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
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
import { ApiError, apiFetch } from "@/lib/api-client";
import { extractApiErrorMessage } from "@/lib/api-error-message";
import { useConfig } from "@/lib/config";

const schema = z
  .object({
    current_password: z.string().min(1, "Current password is required"),
    new_password: z.string().min(8, "At least 8 characters"),
    confirm: z.string().min(1, "Confirm your new password"),
  })
  .refine((values) => values.new_password === values.confirm, {
    message: "Passwords do not match",
    path: ["confirm"],
  });

type FormValues = z.infer<typeof schema>;

/**
 * Settings → Security. Currently just the change-password form; a 2FA setup
 * block can be added as a sibling within this section later without a rewrite.
 */
export function SecuritySection() {
  const { data: config } = useConfig();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { current_password: "", new_password: "", confirm: "" },
  });

  const mutation = useMutation({
    mutationFn: (values: FormValues) =>
      apiFetch("/api/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: values.current_password,
          new_password: values.new_password,
        }),
      }),
    onSuccess: () => {
      form.reset({ current_password: "", new_password: "", confirm: "" });
    },
    onError: () => {
      form.reset({ current_password: "", new_password: "", confirm: "" });
    },
  });

  function onSubmit(values: FormValues) {
    mutation.mutate(values);
  }

  // Hide in demo mode. UI defense-in-depth ONLY: the enforcing control is
  // the 403 from forbid_in_demo on POST /api/auth/password, which blocks a
  // direct API call regardless of the UI.
  if (config?.demo) return null;

  let errorMessage: string | null = null;
  if (mutation.isError) {
    const err = mutation.error;
    if (err instanceof ApiError && err.status === 401) {
      errorMessage = "Current password is incorrect";
    } else {
      errorMessage = extractApiErrorMessage(err, "Could not update password");
    }
  }

  return (
    <section aria-labelledby="security-heading" id="security" className="space-y-4">
      <div className="space-y-1">
        <h2 id="security-heading" className="text-base font-semibold">
          Security
        </h2>
        <p className="text-sm text-muted-foreground">
          Change the password used to sign in.
        </p>
      </div>

      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="max-w-sm space-y-4"
        >
          <FormField
            control={form.control}
            name="current_password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Current password</FormLabel>
                <FormControl>
                  <Input
                    type="password"
                    autoComplete="current-password"
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="new_password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>New password</FormLabel>
                <FormControl>
                  <Input type="password" autoComplete="new-password" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="confirm"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Confirm new password</FormLabel>
                <FormControl>
                  <Input type="password" autoComplete="new-password" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          {errorMessage ? (
            <p className="text-sm text-negative" role="alert">
              {errorMessage}
            </p>
          ) : null}
          {mutation.isSuccess ? (
            <p className="text-sm text-positive" role="status">
              Password updated
            </p>
          ) : null}

          <Button
            type="submit"
            className="min-h-11"
            disabled={mutation.isPending}
          >
            {mutation.isPending ? "Updating…" : "Update password"}
          </Button>
        </form>
      </Form>
    </section>
  );
}
