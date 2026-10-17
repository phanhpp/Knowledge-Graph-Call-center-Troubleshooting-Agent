import type { Metadata } from "next";
import { Inter } from "next/font/google";

import "./globals.css";

// next/font downloads Inter at build time and serves it from this app: no request to Google.
const inter = Inter({ subsets: ["latin"], display: "swap", variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Super Agent",
  description: "A call-centre copilot: troubleshooting walks and knowledge-base answers, grounded in a Neo4j knowledge graph.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      {/* suppressHydrationWarning: browser extensions (e.g. Grammarly) add attributes to <body> before
          React loads, which React would otherwise report as a hydration mismatch. One level deep only. */}
      <body className="h-dvh overflow-hidden" suppressHydrationWarning>
        {children}
      </body>
    </html>
  );
}
