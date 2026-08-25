import type { Metadata } from "next";
import "./globals.css";
import Providers from "./providers";

export const metadata: Metadata = {
  title: "Kor Travel Docker Manager",
  description: "Kor Travel infrastructure container management and metrics dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body className="font-sans antialiased bg-page text-strong min-h-screen">
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  );
}
