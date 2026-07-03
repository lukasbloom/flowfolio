"use client";

import { useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
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
import {
  ProviderKeyStep,
  type WizardProvider,
} from "@/components/setup/ProviderKeyStep";
import { ApiError, apiFetch } from "@/lib/api-client";

interface KeysResponse {
  demo: boolean;
  providers: WizardProvider[];
}

const schema = z
  .object({
    // Mirrors the backend ClaimRequest min_length=8 (defense in depth).
    password: z.string().min(8, "Min 8 characters"),
    confirm: z.string().min(1, "Confirm your password"),
  })
  .refine((values) => values.password === values.confirm, {
    message: "Passwords do not match",
    path: ["confirm"],
  });

type FormValues = z.infer<typeof schema>;

export default function SetupPage() {
  const router = useRouter();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // The wizard lives only in the post-claim session. A hard reload lands
  // the now-authenticated user on /track with no resume, so we keep no
  // server flag and no new route.
  const [phase, setPhase] = useState<"password" | "wizard">("password");
  const [providers, setProviders] = useState<WizardProvider[]>([]);
  const [step, setStep] = useState(0);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { password: "", confirm: "" },
  });

  function finish() {
    router.replace("/track");
    router.refresh();
  }

  // onSaved and onSkip both advance: next step while more remain, else /track.
  function advance() {
    if (step + 1 < providers.length) {
      setStep(step + 1);
    } else {
      finish();
    }
  }

  async function onSubmit(values: FormValues) {
    setSubmitError(null);
    setSubmitting(true);
    try {
      // The claim issues the session cookie, so the user is authenticated
      // immediately.
      await apiFetch("/api/setup/claim", {
        method: "POST",
        body: JSON.stringify({ password: values.password }),
      });
      // Enter the in-page wizard unless demo mode or no providers.
      const keys = await apiFetch<KeysResponse>("/api/keys");
      if (keys.demo || keys.providers.length === 0) {
        finish();
        return;
      }
      setProviders(keys.providers);
      setStep(0);
      setPhase("wizard");
      setSubmitting(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Race: another visitor claimed first — the wizard is now closed.
        setSubmitError(
          "This instance has already been set up — redirecting to sign in",
        );
        router.replace("/login");
        return;
      }
      setSubmitError(err instanceof Error ? err.message : "Setup failed");
      form.reset({ password: "", confirm: "" });
      setSubmitting(false);
    }
  }

  if (phase === "wizard") {
    const provider = providers[step];
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
            <p className="text-sm text-muted-foreground">
              Add API keys to fetch live prices. Skip any you do not have, you
              can add the rest later in Settings.
            </p>
          </div>
          <ProviderKeyStep
            key={provider.id}
            provider={provider}
            stepIndex={step}
            total={providers.length}
            onSaved={advance}
            onSkip={advance}
          />
          <div className="text-center">
            <Button
              type="button"
              variant="link"
              className="text-sm text-muted-foreground"
              onClick={finish}
            >
              Skip all and finish
            </Button>
          </div>
        </div>
      </main>
    );
  }

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
          <p className="text-sm text-muted-foreground">
            Choose a password to secure your Flowfolio instance
          </p>
          <h2 className="text-lg font-medium">Set your password</h2>
        </div>
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
                      autoComplete="new-password"
                      autoFocus
                      {...field}
                    />
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
                  <FormLabel>Confirm password</FormLabel>
                  <FormControl>
                    <Input
                      type="password"
                      autoComplete="new-password"
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
            <Button
              type="submit"
              className="w-full min-h-11"
              disabled={submitting}
            >
              {submitting ? "Setting up…" : "Set password"}
            </Button>
          </form>
        </Form>
      </div>
    </main>
  );
}
