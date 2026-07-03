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

const schema = z.object({
  password: z.string().min(1, "Password required"),
});

type FormValues = z.infer<typeof schema>;

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { password: "" },
  });

  async function onSubmit(values: FormValues) {
    setSubmitError(null);
    setSubmitting(true);
    try {
      await apiFetch("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ password: values.password }),
      });
      // Open-redirect guard: only allow same-origin paths.
      // - "/path"  → same-origin (allowed)
      // - "//foo"  → protocol-relative URL that escapes origin (rejected)
      // - "http://..." → does not start with "/" (rejected)
      const safeNext =
        next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
      router.replace(safeNext);
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setSubmitError("Invalid password");
      } else {
        setSubmitError(err instanceof Error ? err.message : "Login failed");
      }
      form.reset({ password: "" }); // clear password on failure
      setSubmitting(false);
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <FormField
          control={form.control}
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
