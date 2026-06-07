export type FeedItem = {
  ticker: string;
  title?: string;
  path?: "Growth" | "Bulletproof";
  stage?: string;
  impact_score?: number;
  confidence?: number;
  decision?: string;
  reason?: string;
  note?: string;
};

export async function getHealth(): Promise<{ status: string; version?: string }> {
  const r = await fetch("/health");
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}

export async function getDemoFeed(): Promise<{ items: FeedItem[] }> {
  const r = await fetch("/api/v1/decision-feed/demo");
  if (!r.ok) throw new Error(`feed ${r.status}`);
  return r.json();
}
