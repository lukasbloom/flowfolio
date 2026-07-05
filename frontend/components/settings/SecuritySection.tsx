"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
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
 * Settings → Security. Change-password form plus a two-factor authentication
 * block (see TwoFactorAuth below).
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

      <div className="max-w-sm space-y-4 border-t pt-6">
        <TwoFactorAuth />
      </div>
    </section>
  );
}

interface TotpSetupResponse {
  secret: string;
  otpauth_uri: string;
  qr_svg: string;
}

/**
 * Settings → Security → Two-factor authentication. Off: an Enable button
 * starts enrollment (QR + manual secret + a 6-digit confirm code). On: a
 * Disable button asks for the current password. Both mutations invalidate
 * the ["2fa-status"] query on success so the On/Off state reflects the
 * backend immediately.
 */
function TwoFactorAuth() {
  const qc = useQueryClient();
  const statusQuery = useQuery({
    queryKey: ["2fa-status"],
    queryFn: () => apiFetch<{ enabled: boolean }>("/api/auth/2fa"),
  });

  const [enrollment, setEnrollment] = useState<TotpSetupResponse | null>(null);
  const [code, setCode] = useState("");
  const [disabling, setDisabling] = useState(false);
  const [password, setPassword] = useState("");

  const setupMutation = useMutation({
    mutationFn: () =>
      apiFetch<TotpSetupResponse>("/api/auth/2fa/setup", { method: "POST" }),
    onSuccess: (data) => {
      setEnrollment(data);
      setCode("");
    },
  });

  const enableMutation = useMutation({
    mutationFn: (value: string) =>
      apiFetch<void>("/api/auth/2fa/enable", {
        method: "POST",
        body: JSON.stringify({ code: value }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["2fa-status"] });
      setEnrollment(null);
      setCode("");
    },
  });

  const disableMutation = useMutation({
    mutationFn: (value: string) =>
      apiFetch<void>("/api/auth/2fa/disable", {
        method: "POST",
        body: JSON.stringify({ password: value }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["2fa-status"] });
      setDisabling(false);
      setPassword("");
    },
  });

  function cancelEnrollment() {
    setEnrollment(null);
    setCode("");
    enableMutation.reset();
  }

  function submitCode(event: React.FormEvent) {
    event.preventDefault();
    enableMutation.mutate(code);
  }

  function cancelDisable() {
    setDisabling(false);
    setPassword("");
    disableMutation.reset();
  }

  function submitDisable(event: React.FormEvent) {
    event.preventDefault();
    disableMutation.mutate(password);
  }

  let setupError: string | null = null;
  if (setupMutation.isError) {
    setupError = extractApiErrorMessage(
      setupMutation.error,
      "Could not start two-factor setup",
    );
  }

  let enableError: string | null = null;
  if (enableMutation.isError) {
    const err = enableMutation.error;
    enableError =
      err instanceof ApiError && err.status === 400
        ? "Invalid code"
        : extractApiErrorMessage(err, "Could not confirm the code");
  }

  let disableError: string | null = null;
  if (disableMutation.isError) {
    const err = disableMutation.error;
    disableError =
      err instanceof ApiError && err.status === 401
        ? "Password is incorrect"
        : extractApiErrorMessage(
            err,
            "Could not disable two-factor authentication",
          );
  }

  const enabled = statusQuery.data?.enabled ?? false;

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold">Two-factor authentication</h3>
        <p className="text-sm text-muted-foreground">
          {statusQuery.isLoading
            ? "Checking status…"
            : `Currently ${enabled ? "on" : "off"}.`}
        </p>
      </div>

      {!statusQuery.isLoading && !enabled && !enrollment ? (
        <Button
          type="button"
          variant="outline"
          className="min-h-11"
          onClick={() => setupMutation.mutate()}
          disabled={setupMutation.isPending}
        >
          {setupMutation.isPending ? "Preparing…" : "Enable"}
        </Button>
      ) : null}
      {setupError ? (
        <p className="text-sm text-negative" role="alert">
          {setupError}
        </p>
      ) : null}

      {enrollment ? (
        <form onSubmit={submitCode} className="space-y-3">
          <img
            src={enrollment.qr_svg}
            alt="Scan this QR code with your authenticator app"
            className="h-40 w-40"
          />
          <p className="text-sm text-muted-foreground">
            Or enter this key manually:
          </p>
          <p className="rounded-md border bg-muted px-3 py-2 font-mono text-sm break-all">
            {enrollment.secret}
          </p>
          <div className="space-y-1.5">
            <label htmlFor="totp-code" className="text-sm font-medium">
              6-digit code
            </label>
            <Input
              id="totp-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              onChange={(event) =>
                setCode(event.target.value.replace(/\D/g, "").slice(0, 6))
              }
              disabled={enableMutation.isPending}
            />
          </div>
          {enableError ? (
            <p className="text-sm text-negative" role="alert">
              {enableError}
            </p>
          ) : null}
          <div className="flex gap-2">
            <Button
              type="submit"
              className="min-h-11"
              disabled={enableMutation.isPending || code.length === 0}
            >
              {enableMutation.isPending ? "Confirming…" : "Confirm"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="min-h-11"
              onClick={cancelEnrollment}
              disabled={enableMutation.isPending}
            >
              Cancel
            </Button>
          </div>
        </form>
      ) : null}

      {enabled && !disabling ? (
        <Button
          type="button"
          variant="outline"
          className="min-h-11"
          onClick={() => setDisabling(true)}
        >
          Disable
        </Button>
      ) : null}

      {disabling ? (
        <form onSubmit={submitDisable} className="space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="disable-password" className="text-sm font-medium">
              Confirm your password
            </label>
            <Input
              id="disable-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={disableMutation.isPending}
            />
          </div>
          {disableError ? (
            <p className="text-sm text-negative" role="alert">
              {disableError}
            </p>
          ) : null}
          <div className="flex gap-2">
            <Button
              type="submit"
              variant="destructive"
              className="min-h-11"
              disabled={disableMutation.isPending || password.length === 0}
            >
              {disableMutation.isPending ? "Disabling…" : "Confirm disable"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="min-h-11"
              onClick={cancelDisable}
              disabled={disableMutation.isPending}
            >
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}
