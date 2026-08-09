"use client";

import { useState } from "react";

import { SystemCheckPanel } from "./SystemCheckPanel";

type View = "overview" | "rankings" | "picks" | "runs";
type Stock = {
  rank: number;
  symbol: string;
  name: string;
  score: number;
  value: number;
  quality: number;
  momentum: number;
  dilution: number;
};

const STOCKS: Stock[] = [
  { rank: 1, symbol: "RCKY", name: "Rocky Brands", score: 71.9, value: 81.8, quality: 63.5, momentum: 91.5, dilution: 0 },
  { rank: 2, symbol: "WLYB", name: "John Wiley & Sons", score: 70.2, value: 77.5, quality: 69.6, momentum: 86.9, dilution: 0 },
  { rank: 3, symbol: "ESCA", name: "Escalade", score: 69.1, value: 74.2, quality: 78.2, momentum: 78.0, dilution: 0 },
  { rank: 4, symbol: "COKE", name: "Coca-Cola Consolidated", score: 68.4, value: 98.3, quality: 63.0, momentum: 66.5, dilution: 0 },
  { rank: 5, symbol: "UTMD", name: "Utah Medical Products", score: 66.6, value: 75.8, quality: 74.0, momentum: 72.2, dilution: 0 },
  { rank: 6, symbol: "PLNT", name: "Planet Fitness", score: 63.2, value: 99.6, quality: 64.2, momentum: 33.1, dilution: 0 },
  { rank: 7, symbol: "CBZ", name: "CBIZ", score: 61.9, value: 71.6, quality: 56.1, momentum: 80.6, dilution: 1.6 },
  { rank: 8, symbol: "MDXG", name: "MiMedx Group", score: 61.9, value: 79.4, quality: 73.9, momentum: 53.0, dilution: 0 },
  { rank: 9, symbol: "WHD", name: "Cactus", score: 61.9, value: 47.8, quality: 77.1, momentum: 81.4, dilution: 0 },
  { rank: 10, symbol: "ACIW", name: "ACI Worldwide", score: 61.7, value: 53.0, quality: 66.9, momentum: 84.9, dilution: 0 },
  { rank: 11, symbol: "DPZ", name: "Domino's Pizza", score: 60.7, value: 72.2, quality: 76.8, momentum: 53.3, dilution: 0 },
  { rank: 12, symbol: "KTCC", name: "Key Tronic", score: 60.6, value: 85.7, quality: 39.3, momentum: 77.0, dilution: 0 },
  { rank: 13, symbol: "SKY", name: "Champion Homes", score: 60.3, value: 65.0, quality: 83.9, momentum: 52.0, dilution: 0 },
  { rank: 14, symbol: "LW", name: "Lamb Weston", score: 59.6, value: 64.8, quality: 53.8, momentum: 69.3, dilution: 0 },
  { rank: 15, symbol: "BOOM", name: "DMC Global", score: 58.6, value: 96.7, quality: 47.5, momentum: 51.1, dilution: 0 },
  { rank: 16, symbol: "ITRI", name: "Itron", score: 58.2, value: 70.6, quality: 61.0, momentum: 62.3, dilution: 0 },
];

const DESCRIPTIONS: Record<string, string> = {
  RCKY: "Designs and sells work, outdoor and western footwear and apparel.",
  WLYB: "Publishes research, professional and learning content.",
  ESCA: "Makes sporting goods for recreation and fitness.",
  COKE: "Manufactures and distributes Coca-Cola beverages in the United States.",
  UTMD: "Makes specialty medical devices, especially for women and babies.",
  PLNT: "Franchises and operates low-cost fitness centers.",
  CBZ: "Provides accounting, tax, advisory, benefits and insurance services.",
  MDXG: "Develops placental biologic products for wound care and surgery.",
};

const RUNS = [
  ["Selection", "Partial", "Aug 6, 2026", "0 candidates"],
  ["Scoring", "Success", "Aug 6, 2026", "896 records"],
  ["Risk flags", "Success", "Aug 6, 2026", "9,100 records"],
  ["Risk flags", "Success", "Aug 6, 2026", "2,600 records"],
  ["Dilution", "Success", "Aug 5, 2026", "700 records"],
];

function StockCard({ stock, open }: { stock: Stock; open: (stock: Stock) => void }) {
  return (
    <button className="stock-card" onClick={() => open(stock)} aria-label={`Open ${stock.symbol} research`}>
      <div className="stock-head"><span>#{stock.rank}</span><strong>{stock.symbol}</strong><b>{stock.score.toFixed(1)}</b></div>
      <small>{stock.name}</small>
      <div className="score-line"><i style={{ width: `${stock.score}%` }} /></div>
      <div className="components">
        <span><small>Value</small>{stock.value.toFixed(1)}</span>
        <span><small>Quality</small>{stock.quality.toFixed(1)}</span>
        <span><small>Momentum</small>{stock.momentum.toFixed(1)}</span>
        <span><small>Dilution</small>{stock.dilution.toFixed(1)}</span>
      </div>
      <em>Ranked, not selected</em><span className="open-label">Open →</span>
    </button>
  );
}

export default function Home() {
  const [view, setView] = useState<View>("overview");
  const [selected, setSelected] = useState<Stock | null>(null);

  const navigate = (next: View) => { setSelected(null); setView(next); window.scrollTo({ top: 0, behavior: "smooth" }); };

  return (
    <div className="site-shell">
      <div className="validation-strip">Engineering validation dataset — not strategy performance</div>
      <header className="topbar">
        <button className="brand" onClick={() => navigate("overview")}><span>SB</span><div><strong>stockbot</strong><small>evidence-led research</small></div></button>
        <nav aria-label="Main navigation">
          {(["overview", "rankings", "picks", "runs"] as View[]).map((item) => (
            <button key={item} className={view === item && !selected ? "active" : ""} onClick={() => navigate(item)}>{item}</button>
          ))}
        </nav>
      </header>

      <main>
        {selected ? (
          <section className="stock-detail">
            <button className="back" onClick={() => setSelected(null)}>← Back to rankings</button>
            <p className="kicker">Rank #{selected.rank} · latest public snapshot</p>
            <div className="detail-title"><div><h1>{selected.symbol}</h1><p>{selected.name}</p></div><strong>{selected.score.toFixed(1)}</strong></div>
            <div className="company-box"><span>What the company does</span><p>{DESCRIPTIONS[selected.symbol] ?? `${selected.name} is a listed company tracked by Stockbot.`}</p></div>
            <section className="score-panel">
              <h2>Score breakdown</h2>
              {([['Value', selected.value], ['Quality', selected.quality], ['Momentum', selected.momentum], ['Dilution penalty', selected.dilution]] as [string, number][]).map(([label, value]) => (
                <div className="metric-row" key={label}><span>{label}</span><div><i style={{ width: `${Math.min(100, value)}%` }} /></div><b>{value.toFixed(1)}</b></div>
              ))}
            </section>
            <div className="privacy-note"><strong>Why no public price chart?</strong><p>Raw price history remains in the private local dashboard because its provider does not license public redistribution.</p></div>
          </section>
        ) : view === "overview" ? (
          <>
            <section className="hero"><div><p className="kicker">Live stock research</p><h1>Your stockbot,<br />without the noise.</h1><p>Rankings, weekly decisions and pipeline activity in short, clear cards.</p><div className="hero-actions"><button onClick={() => navigate("rankings")}>View rankings</button><button onClick={() => navigate("picks")}>See weekly picks</button></div></div><aside><span>Experiment</span><strong>LIVE</strong><dl><div><dt>ID</dt><dd>exp-d59006eb199b</dd></div><div><dt>Strategy</dt><dd>v2</dd></div><div><dt>Selected</dt><dd>0</dd></div></dl></aside></section>
            <section className="metric-grid"><article><span>Universe evaluated</span><strong>937</strong><small>367 included</small></article><article><span>Ranked stocks</span><strong>97</strong><small>Aug 3 snapshot</small></article><article><span>Published candidates</span><strong>0</strong><small>valid bot result</small></article><SystemCheckPanel /></section>
            <section className="section-head"><div><p className="kicker">Latest deterministic ranking</p><h2>Top ranked stocks</h2></div><button onClick={() => navigate("rankings")}>View all →</button></section>
            <section className="stock-grid">{STOCKS.slice(0, 8).map(stock => <StockCard stock={stock} open={setSelected} key={stock.symbol} />)}</section>
          </>
        ) : view === "rankings" ? (
          <><section className="page-head"><p className="kicker">Latest bot scores</p><h1>Top ranked stocks</h1><p>High score does not mean selected. Open a card to inspect the score.</p><div className="inline-stats"><span><small>Ranked in model</small><strong>97</strong></span><span><small>Shown here</small><strong>16</strong></span><span><small>Actually selected</small><strong>0</strong></span></div></section><section className="stock-grid">{STOCKS.map(stock => <StockCard stock={stock} open={setSelected} key={stock.symbol} />)}</section></>
        ) : view === "picks" ? (
          <section className="page-head"><p className="kicker">Latest weekly decision</p><h1>Picked stocks</h1><div className="empty-result"><strong>0 stocks selected</strong><p>This is a real result. The bot refused to assume missing risk evidence was safe.</p></div><h2>Filter result</h2><div className="check-list"><span className="pass"><b>✓</b> In stock universe <em>Passed</em></span><span className="pass"><b>✓</b> Composite scores <em>Passed</em></span><span className="unknown"><b>?</b> Risk-flag evidence <em>Missing</em></span><span className="fail"><b>✕</b> Final eligibility <em>Failed</em></span></div><p className="plain-note">50 stocks were considered across the 20-day and 60-day research books. Missing evidence blocked selection; it was not converted into a fake zero.</p></section>
        ) : (
          <section className="page-head"><p className="kicker">Pipeline activity</p><h1>Run history</h1><p>The latest recorded bot jobs, newest first.</p><div className="run-grid">{RUNS.map(([stage, status, date, records], index) => <article key={`${stage}-${index}`}><div><strong>{stage}</strong><span className={status.toLowerCase()}>{status}</span></div><p>{date}</p><small>{records}</small></article>)}</div></section>
        )}
      </main>

      <footer><strong>Stockbot</strong><p>Personal research tool. Not financial advice. Rankings are model outputs, not buy recommendations.</p><a href="https://github.com/khorhaoxiang88-svg/stockbot" target="_blank" rel="noreferrer">View source on GitHub ↗</a></footer>
    </div>
  );
}
