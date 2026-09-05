import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { ThemeProvider } from "@/components/theme";
import { BRAND, brandCssVars } from "@/lib/brand";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: BRAND.name,
    template: `%s · ${BRAND.name}`,
  },
  description: `${BRAND.name} — ${BRAND.tagline.toLowerCase()}, intake to shipped at the autonomy you set.`,
  applicationName: BRAND.name,
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f5fa" },
    { media: "(prefers-color-scheme: dark)", color: "#08090d" },
  ],
  colorScheme: "light dark",
};

/**
 * Applies the persisted theme before hydration so there is no flash of the wrong theme.
 * Default is **light** (the product owner's preference); `dark` and `system` are opt-in.
 */
const themeInitScript = `(function () {
  try {
    var stored = localStorage.getItem("foundry.theme") || "light";
    var resolved = stored === "system"
      ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : stored;
    document.documentElement.setAttribute("data-theme", stored);
    document.documentElement.style.colorScheme = resolved;
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "light");
  }
})();`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-theme="light" style={{ colorScheme: "light" }} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Sora:wght@600;700;800&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        {/* Brand accent tokens from lib/brand.ts — after the token stylesheet so they win. */}
        <style dangerouslySetInnerHTML={{ __html: brandCssVars() }} />
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
