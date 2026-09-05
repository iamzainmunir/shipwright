import { LiveBuild } from "@/components/live-build";

/** Live Build route. In Next 15 route `params` is async. */
export default async function MissionRunPage({
  params,
}: {
  params: Promise<{ key: string }>;
}) {
  const { key } = await params;
  return <LiveBuild missionKey={key} />;
}
