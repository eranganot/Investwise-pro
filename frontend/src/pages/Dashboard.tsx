import { useEffect, useState } from "react";
import { getDemoFeed, getHealth, type FeedItem } from "../api";

export default function Dashboard() {
  const [health, setHealth] = useState<string>("…");
  const [items, setItems] = useState<FeedItem[]>([]);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    getHealth()
      .then((h) => setHealth(h.status))
      .catch(() => setHealth("offline"));
    getDemoFeed()
      .then((d) => setItems(d.items || []))
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="app">
      <header>
        <div>
          <h1>InvestWise Pro</h1>
          <p className="subtitle">Decision Feed · Phase 0 skeleton (stub engines)</p>
        </div>
        <span className={`badge ${health === "ok" ? "ok" : "bad"}`}>
          API: {health}
        </span>
      </header>

      {error && <div className="error">Backend not reachable: {error}</div>}

      <div className="feed">
        {items.length === 0 && !error && <p className="muted">Loading feed…</p>}
        {items.map((it, i) => (
          <div className="card" key={i}>
            <div className="card-head">
              <strong>{it.title || it.ticker}</strong>
              {it.path && (
                <span className={`pill ${it.path === "Growth" ? "growth" : "bulletproof"}`}>
                  {it.path}
                </span>
              )}
            </div>
            {it.decision ? (
              <p className="muted">
                {it.decision}
                {it.reason ? ` — ${it.reason}` : ""}
              </p>
            ) : (
              <div className="metrics">
                <span>Impact <b>{it.impact_score}</b></span>
                <span>Confidence <b>{it.confidence}%</b></span>
              </div>
            )}
            {it.note && <p className="note">{it.note}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}
