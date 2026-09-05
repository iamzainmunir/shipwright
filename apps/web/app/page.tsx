import { redirect } from "next/navigation";

/**
 * App root → Command Center.
 *
 * The marketing landing page moved to a standalone website (the Shipwright site repo). Within this
 * app it is NOT removed — it is preserved verbatim at `/landing` (app/landing/page.tsx) and can be
 * restored as the root at any time by swapping this file back for that component. The product app
 * now opens straight on the dashboard.
 */
export default function Home() {
  redirect("/dashboard");
}
