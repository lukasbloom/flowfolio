import { Suspense } from "react";

import { TxnList } from "@/components/transactions/TxnList";

export default function TransactionsPage() {
  return (
    <main className="mx-auto max-w-7xl px-4 py-6 md:px-6 md:py-8">
      <h1 className="text-2xl font-semibold leading-tight">Activity</h1>
      <Suspense fallback={null}>
        <TxnList />
      </Suspense>
    </main>
  );
}
