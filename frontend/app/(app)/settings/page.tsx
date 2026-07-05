import { Separator } from "@/components/ui/separator";
import { AccountsSection } from "@/components/settings/AccountsSection";
import { ConcentrationSettings } from "@/components/settings/ConcentrationSettings";
import { DataSourcesSection } from "@/components/settings/DataSourcesSection";
import { MutedHoldingsList } from "@/components/settings/MutedHoldingsList";
import { PriceHistorySection } from "@/components/settings/PriceHistorySection";
import { SecuritySection } from "@/components/settings/SecuritySection";
import { SoftwareUpdatesSection } from "@/components/settings/SoftwareUpdatesSection";
import { UnclassifiedHint } from "@/components/settings/UnclassifiedHint";

export default function SettingsPage() {
  return (
    <main className="mx-auto max-w-7xl px-4 py-6 md:px-6 md:py-8">
      <h1 className="text-2xl font-semibold leading-tight">Settings</h1>

      <section className="mt-6 max-w-2xl">
        <AccountsSection />
      </section>

      <Separator className="my-8" />

      <section className="max-w-2xl">
        <PriceHistorySection />
      </section>

      <Separator className="my-8" />

      <section className="max-w-2xl">
        <ConcentrationSettings />
      </section>

      <Separator className="my-8" />

      <section className="max-w-2xl">
        <MutedHoldingsList />
      </section>

      <Separator className="my-8" />

      <section className="max-w-2xl">
        <UnclassifiedHint />
      </section>

      <Separator className="my-8" />

      <section className="max-w-2xl">
        <SoftwareUpdatesSection />
      </section>

      <Separator className="my-8" />

      <section className="max-w-2xl">
        <SecuritySection />
      </section>

      <Separator className="my-8" />

      <section className="max-w-2xl">
        <DataSourcesSection />
      </section>
    </main>
  );
}
