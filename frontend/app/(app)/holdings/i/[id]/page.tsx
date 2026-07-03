import { InstrumentTabs } from "./InstrumentTabs";

export default async function InstrumentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <main className="mx-auto max-w-7xl px-4 py-6 md:px-6 md:py-8">
      <InstrumentTabs id={id} />
    </main>
  );
}
