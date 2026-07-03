export default function InstrumentsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <main className="mx-auto max-w-7xl px-4 py-6 md:px-6 md:py-8">
      <h1 className="mb-2 text-2xl font-semibold leading-tight">Instruments</h1>
      <div className="mt-4">{children}</div>
    </main>
  );
}
