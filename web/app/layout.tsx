import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { ExperimentBanner } from "@/components/experiment-banner";
import { PhaseBanner } from "@/components/phase-banner";
import { SiteFooter } from "@/components/site-footer";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "stockbot",
  description: "US stock research system",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // Dark is the only theme. High contrast, large type.
    <html lang="en" className="dark">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <PhaseBanner />
        <ExperimentBanner />
        {children}
        <SiteFooter />
      </body>
    </html>
  );
}
