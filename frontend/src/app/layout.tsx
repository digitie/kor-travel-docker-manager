import type { Metadata } from "next";
import { Inter, Saira_Condensed } from "next/font/google";
import "./globals.css";
import Providers from "./providers";

const inter = Inter({
  subsets: ["latin"],
  weight: ["300", "400", "700"],
  variable: "--font-inter",
});

const sairaCondensed = Saira_Condensed({
  subsets: ["latin"],
  weight: ["700"],
  variable: "--font-saira-condensed",
});

export const metadata: Metadata = {
  title: "TripMate Manager",
  description: "PostgreSQL & RustFS Container Management Dashboard for TripMate Developers",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" className={`${inter.variable} ${sairaCondensed.variable}`}>
      <body className="font-sans antialiased bg-background text-foreground min-h-screen">
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  );
}
