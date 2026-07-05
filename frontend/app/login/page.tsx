"use client";

import { Suspense, useState } from "react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
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

const passwordSchema = z.object({
  password: z.string().min(1, "Password required"),
});

const codeSchema = z.object({
  code: z.string().min(1, "Code required"),
});

type PasswordFormValues = z.infer<typeof passwordSchema>;
type CodeFormValues = z.infer<typeof codeSchema>;

// Body of POST /api/auth/login. 2FA off: {status: "ok"} (cookie set).
// 2FA on: {twofa_required: "true", pre_auth_token} (no cookie).
type LoginResponse = {
  status?: string;
  twofa_required?: string;
  pre_auth_token?: string;
};

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [preAuthToken, setPreAuthToken] = useState<string | null>(null);

  const passwordForm = useForm<PasswordFormValues>({
    resolver: zodResolver(passwordSchema),
    defaultValues: { password: "" },
  });

  const codeForm = useForm<CodeFormValues>({
    resolver: zodResolver(codeSchema),
    defaultValues: { code: "" },
  });

  function redirectAfterLogin() {
    // Open-redirect guard: only allow same-origin paths.
    // - "/path"  → same-origin (allowed)
    // - "//foo"  → protocol-relative URL that escapes origin (rejected)
    // - "http://..." → does not start with "/" (rejected)
    const safeNext =
      next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
    router.replace(safeNext);
    router.refresh();
  }

  async function onSubmitPassword(values: PasswordFormValues) {
    setSubmitError(null);
    setSubmitting(true);
    try {
      const response = await apiFetch<LoginResponse>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ password: values.password }),
      });
      if (response?.twofa_required === "true" && response.pre_auth_token) {
        setPreAuthToken(response.pre_auth_token);
        setSubmitting(false);
        return;
      }
      redirectAfterLogin();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setSubmitError("Invalid password");
      } else {
        setSubmitError(err instanceof Error ? err.message : "Login failed");
      }
      passwordForm.reset({ password: "" }); // clear password on failure
      setSubmitting(false);
    }
  }

  async function onSubmitCode(values: CodeFormValues) {
    setSubmitError(null);
    setSubmitting(true);
    try {
      await apiFetch("/api/auth/login/2fa", {
        method: "POST",
        body: JSON.stringify({ pre_auth_token: preAuthToken, code: values.code }),
      });
      redirectAfterLogin();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        // Keep the pre_auth_token so the user can retry with a fresh code.
        // If the token itself has since expired the retry just 401s again,
        // which is acceptable (matches the task's stated tradeoff).
        setSubmitError("Invalid code");
      } else {
        setSubmitError(err instanceof Error ? err.message : "Login failed");
      }
      codeForm.reset({ code: "" });
      setSubmitting(false);
    }
  }

  if (preAuthToken) {
    return (
      <Form {...codeForm}>
        <form onSubmit={codeForm.handleSubmit(onSubmitCode)} className="space-y-4">
          <FormField
            control={codeForm.control}
            name="code"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Authentication code</FormLabel>
                <FormControl>
                  <Input
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    autoFocus
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          {submitError ? (
            <p className="text-sm text-negative" role="alert">
              {submitError}
            </p>
          ) : null}
          <Button type="submit" className="w-full min-h-11" disabled={submitting}>
            {submitting ? "Verifying…" : "Verify"}
          </Button>
        </form>
      </Form>
    );
  }

  return (
    <Form {...passwordForm}>
      <form
        onSubmit={passwordForm.handleSubmit(onSubmitPassword)}
        className="space-y-4"
      >
        <FormField
          control={passwordForm.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Password</FormLabel>
              <FormControl>
                <Input
                  type="password"
                  autoComplete="current-password"
                  autoFocus
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        {submitError ? (
          <p className="text-sm text-negative" role="alert">
            {submitError}
          </p>
        ) : null}
        <Button type="submit" className="w-full min-h-11" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </Form>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-8">
        <div className="space-y-2 text-center">
          <h1 className="flex items-center justify-center gap-2 text-2xl font-semibold tracking-tight">
            <Image
              src="/logo-mark.png"
              alt=""
              width={1060}
              height={503}
              priority
              className="h-8 w-auto"
            />
            Flowfolio
          </h1>
          <p className="text-sm text-muted-foreground">Sign in to continue</p>
        </div>
        {/*
         * Suspense boundary required by Next.js 16 because LoginForm calls
         * useSearchParams(). Without it, the static prerender of /login
         * fails with "useSearchParams() should be wrapped in a suspense
         * boundary" (CSR bailout error).
         */}
        <Suspense fallback={null}>
          <LoginForm />
        </Suspense>
      </div>
    </main>
  );
}
