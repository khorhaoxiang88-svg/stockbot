import Link from "next/link";

const links = [
  ["Overview", "/"],
  ["Rankings", "/rankings"],
  ["Picked stocks", "/candidates"],
  ["All stocks", "/universe"],
  ["Results", "/performance"],
  ["Runs", "/runs"],
  ["Health", "/health"],
  ["Changelog", "/changelog"],
] as const;

export function SiteNav() {
  return (
    <nav className="site-nav" aria-label="Main navigation">
      <Link href="/" className="site-brand" aria-label="Stockbot overview">
        <span className="site-brand-mark">SB</span>
        <span>
          <strong>stockbot</strong>
          <small>evidence-led research</small>
        </span>
      </Link>
      <div className="site-nav-links">
        {links.map(([label, href]) => (
          <Link key={href} href={href}>{label}</Link>
        ))}
      </div>
    </nav>
  );
}
