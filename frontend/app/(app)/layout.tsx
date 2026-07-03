import { cookies } from "next/headers";

import { AppHeader } from "@/components/header/AppHeader";
import { MobileAppBar } from "@/components/header/MobileAppBar";
import { CurrencyProvider } from "@/lib/currency";
import { PrefsProvider } from "@/lib/prefs";
import { TagFilterProvider } from "@/lib/tag-filter";
import { TagsManagerProvider } from "@/components/tags/TagsManagerProvider";
import { TagsManager } from "@/components/tags/TagsManager";
import { AddTxnProvider } from "@/components/transactions/AddTxnProvider";
import { AddTxnPicker } from "@/components/transactions/AddTxnPicker";
import { AddTxnFormSheet } from "@/components/transactions/AddTxnFormSheet";
import { MoreSheetProvider } from "@/components/header/MoreSheetProvider";
import { MoreSheet } from "@/components/header/MoreSheet";
import { BottomTabBar } from "@/components/header/BottomTabBar";
import { AddTxnFab } from "@/components/header/AddTxnFab";
import { ConcentrationBannerProvider } from "@/components/concentration/ConcentrationBannerProvider";
import { UpdateBannerProvider } from "@/components/update/UpdateBannerProvider";
import { UpdateBanner } from "@/components/update/UpdateBanner";
import { DemoBanner } from "@/components/demo/DemoBanner";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  // Persisted UI prefs live in flowfolio.* cookies so the
  // server renders the stored values (no default→stored flash after
  // hydration, no throwaway default-key fetches). Values are URL-encoded on
  // write (lib/prefs.tsx); decode defensively in case the framework already
  // decoded them.
  const cookieStore = await cookies();
  const initialPrefs: Record<string, string> = {};
  for (const cookie of cookieStore.getAll()) {
    if (!cookie.name.startsWith("flowfolio.")) continue;
    try {
      initialPrefs[cookie.name] = decodeURIComponent(cookie.value);
    } catch {
      initialPrefs[cookie.name] = cookie.value;
    }
  }

  return (
    <PrefsProvider initial={initialPrefs}>
      <CurrencyProvider>
        <TagFilterProvider>
          <TagsManagerProvider>
            <MoreSheetProvider>
              <AddTxnProvider>
                <ConcentrationBannerProvider>
                  <UpdateBannerProvider>
                    <AppHeader />
                    <MobileAppBar />
                    <main className="pb-20 md:pb-0">
                      {/* Collapses to nothing when UpdateBanner renders null
                          (up to date / dismissed) so absent updates add no
                          stray top padding on any page. */}
                      {/* Both wrappers collapse to nothing when their banner
                          renders null (DemoBanner outside demo mode, UpdateBanner
                          when up to date / dismissed) so absent banners add no
                          stray top padding on any page. */}
                      <div className="mx-auto w-full max-w-7xl px-4 pt-6 md:px-6 md:pt-8 [&:empty]:hidden">
                        <DemoBanner />
                      </div>
                      <div className="mx-auto w-full max-w-7xl px-4 pt-6 md:px-6 md:pt-8 [&:empty]:hidden">
                        <UpdateBanner />
                      </div>
                      {children}
                    </main>
                    <TagsManager />
                    <AddTxnPicker />
                    <AddTxnFormSheet />
                    <BottomTabBar />
                    <AddTxnFab />
                    <MoreSheet />
                  </UpdateBannerProvider>
                </ConcentrationBannerProvider>
              </AddTxnProvider>
            </MoreSheetProvider>
          </TagsManagerProvider>
        </TagFilterProvider>
      </CurrencyProvider>
    </PrefsProvider>
  );
}
