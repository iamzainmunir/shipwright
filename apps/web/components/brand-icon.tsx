import type { ReactNode } from "react";

/**
 * BrandIcon — real vendor logos for integrations (GitHub, Slack, Jira, …).
 *
 * Each glyph is a 24×24 SVG in the vendor's own mark and brand color. Monochrome brands
 * (GitHub, Vercel) use `var(--text)` so they invert with the theme; multi-color brands
 * (Slack, Figma) carry their fixed brand colors. Unknown kinds fall back to a branded
 * letter tile so the grid never shows a blank.
 */
export interface BrandIconProps {
  kind: string;
  name: string;
  size?: number;
}

// Brand accent used for lettermark fallbacks (kinds without a bespoke glyph).
const FALLBACK_COLOR: Record<string, string> = {
  gitlab: "#FC6D26",
  postgres: "#4169E1",
  mongo: "#47A248",
  datadog: "#632CA6",
  pagerduty: "#06AC38",
  notion: "var(--text)",
  bitbucket: "#0052CC",
};

function Svg({ children, size, fill }: { children: ReactNode; size: number; fill?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill ?? "none"} aria-hidden="true">
      {children}
    </svg>
  );
}

export function BrandIcon({ kind, name, size = 26 }: BrandIconProps) {
  switch (kind) {
    case "github":
      return (
        <Svg size={size} fill="var(--text)">
          <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
        </Svg>
      );
    case "vercel":
      return (
        <Svg size={size} fill="var(--text)">
          <path d="M12 1.5l11 19.5H1z" />
        </Svg>
      );
    case "jira":
      return (
        <Svg size={size} fill="#2684FF">
          <path d="M11.571 11.513H0a5.218 5.218 0 0 0 5.232 5.215h2.13v2.057A5.215 5.215 0 0 0 12.575 24V12.518a1.005 1.005 0 0 0-1.005-1.005z" />
          <path fill="#2684FF" opacity="0.75" d="M17.294 5.757H5.736a5.215 5.215 0 0 0 5.215 5.214h2.129v2.058a5.218 5.218 0 0 0 5.215 5.214V6.758a1.001 1.001 0 0 0-1-1.001z" />
          <path fill="#2684FF" opacity="0.55" d="M23.013 0H11.455a5.215 5.215 0 0 0 5.215 5.215h2.129v2.057A5.215 5.215 0 0 0 24 12.483V1.005A1.001 1.001 0 0 0 23.013 0z" />
        </Svg>
      );
    case "slack":
      return (
        <Svg size={size}>
          <path fill="#36C5F0" d="M8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834z" />
          <path fill="#36C5F0" d="M8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312z" />
          <path fill="#2EB67D" d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52z" />
          <path fill="#2EB67D" d="M6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z" />
          <path fill="#ECB22E" d="M18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834z" />
          <path fill="#ECB22E" d="M17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312z" />
          <path fill="#E01E5A" d="M15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52z" />
          <path fill="#E01E5A" d="M15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" />
        </Svg>
      );
    case "figma":
      return (
        <Svg size={size}>
          <path fill="#F24E1E" d="M12 0H8a4 4 0 0 0 0 8h4V0z" />
          <path fill="#A259FF" d="M12 8H8a4 4 0 0 0 0 8h4V8z" />
          <path fill="#0ACF83" d="M8 16a4 4 0 1 0 4 4v-4H8z" />
          <path fill="#FF7262" d="M12 0h4a4 4 0 0 1 0 8h-4V0z" />
          <path fill="#1ABCFE" d="M20 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0z" />
        </Svg>
      );
    case "chrome":
      return (
        <Svg size={size} fill="#4285F4">
          <path d="M12 0C8.21 0 4.831 1.757 2.632 4.501l3.953 6.848A5.454 5.454 0 0 1 12 6.545h10.691A12 12 0 0 0 12 0zM1.931 5.47A11.943 11.943 0 0 0 0 12c0 6.012 4.42 10.991 10.189 11.864l3.953-6.847a5.45 5.45 0 0 1-6.865-2.29zm13.342 2.166a5.446 5.446 0 0 1 1.45 7.09l.002.001h-.002l-5.344 9.257c.206.01.413.016.621.016 6.627 0 12-5.373 12-12 0-1.54-.29-3.011-.818-4.364zM12 16.364a4.364 4.364 0 1 1 0-8.728 4.364 4.364 0 0 1 0 8.728z" />
        </Svg>
      );
    case "linear":
      return (
        <Svg size={size} fill="#5E6AD2">
          <path d="M1.225 15.607A12.02 12.02 0 0 0 8.394 22.776L1.225 15.607ZM.19 11.31 12.69 23.81a12.06 12.06 0 0 0 2.98-.72L.91 8.33a12.06 12.06 0 0 0-.72 2.98ZM1.61 6.104 17.898 22.39a12.1 12.1 0 0 0 2.02-1.612L3.222 4.083A12.1 12.1 0 0 0 1.61 6.104ZM4.533 2.79 21.21 19.467A12 12 0 0 0 4.533 2.79Z" />
        </Svg>
      );
    case "sentry":
      return (
        <Svg size={size} fill="#6C5FC7">
          <path d="M13.5 5.22a1.73 1.73 0 0 0-3 0L2.6 18.78c-.63 1.08.15 2.44 1.4 2.44h3.86a.62.62 0 0 0 .55-.9 7.7 7.7 0 0 0-3.02-3.16l1.02-1.77a9.76 9.76 0 0 1 4.3 5.4.62.62 0 0 0 .59.43h6.7c1.25 0 2.03-1.36 1.4-2.44Zm-1.5 2.33 5.86 10.15h-3.2a11.98 11.98 0 0 0-4.5-5.19l1.06-1.83Z" />
        </Svg>
      );
    default: {
      const color = FALLBACK_COLOR[kind] ?? "var(--brand)";
      return (
        <span
          style={{
            fontFamily: "var(--display)",
            fontWeight: 800,
            fontSize: size * 0.66,
            lineHeight: 1,
            color,
          }}
          aria-hidden="true"
        >
          {name.charAt(0).toUpperCase()}
        </span>
      );
    }
  }
}
