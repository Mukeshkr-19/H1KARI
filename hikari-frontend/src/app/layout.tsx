import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HIKARI - Personal AI Assistant",
  description: "Multi-agent autonomous AI assistant with voice authentication",
  manifest: "/manifest.json",
  icons: {
    icon: "/icon-192.png",
    apple: "/icon-192.png",
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "HIKARI",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased bg-[#0a0a0f] text-white min-h-screen">{children}</body>
    </html>
  );
}
