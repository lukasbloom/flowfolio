import { redirect } from "next/navigation";
import { Suspense } from "react";

import { ReconciliationForm } from "@/components/reconciliation/ReconciliationForm";

export default async function ReconcilePage({
  searchParams,
}: {
  searchParams: Promise<{ account?: string }>;
}) {
  const { account } = await searchParams;
  if (!account) {
    redirect("/settings");
  }
  return (
    <main className="mx-auto max-w-5xl px-4 py-6 md:px-8 md:py-8">
      <Suspense fallback={null}>
        <ReconciliationForm accountId={account} />
      </Suspense>
    </main>
  );
}
