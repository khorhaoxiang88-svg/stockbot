import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  metadataBase: new URL("https://stockbot-khorhao.yyx2620.chatgpt.site"),
  title: "Stockbot — Evidence-led stock research",
  description: "Transparent stock rankings, weekly decisions and pipeline activity.",
  openGraph: {
    title: "Stockbot — Evidence-led stock research",
    description: "Transparent stock rankings, weekly decisions and pipeline activity.",
    images: ["/og.png"],
  },
  twitter: {
    card: "summary_large_image",
    title: "Stockbot — Evidence-led stock research",
    description: "Transparent stock rankings, weekly decisions and pipeline activity.",
    images: ["/og.png"],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body className={`${geistSans.variable} ${geistMono.variable}`}>{children}</body></html>;
}
