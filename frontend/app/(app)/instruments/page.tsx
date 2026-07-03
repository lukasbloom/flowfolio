import { Suspense } from "react";
import { InstrumentList } from "@/components/instruments/InstrumentList";

export default function InstrumentsPage() {
  return (
    <Suspense fallback={null}>
      <InstrumentList />
    </Suspense>
  );
}
