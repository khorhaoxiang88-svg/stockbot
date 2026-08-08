import type { Metadata } from "next";

import { ExperimentBanner } from "@/components/experiment-banner";
import { SiteFooter } from "@/components/site-footer";
import { SiteNav } from "@/components/site-nav";

import "./globals.css";

export const metadata: Metadata = {
  title: "stockbot — Evidence-led US stock research",
  description: "Auditable stock scores, weekly research candidates, risk flags and forward paper results.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // Dark is the only theme. High contrast, large type.
    <html lang="en" className="dark">
      <body className="antialiased">
        <ExperimentBanner />
        <SiteNav />
        {children}
        <SiteFooter />
      </body>
    </html>
  );
}
