/**
 * Server-rendered SVG price chart. No charting library and no client JavaScript:
 * the series is small enough to draw as a single path, and keeping it on the
 * server means the page has no hydration cost and no external dependency.
 */

export type ChartPoint = { date: string; close: number | null };

export type ChartMarker = {
  date: string;
  label: string;
  kind: "split" | "dividend";
};

type Props = {
  points: ChartPoint[];
  markers?: ChartMarker[];
  title: string;
  subtitle?: string;
  height?: number;
  accent?: string;
};

const WIDTH = 1000;
const PAD_LEFT = 78;
const PAD_RIGHT = 20;
const PAD_TOP = 20;
const PAD_BOTTOM = 46;

function formatPrice(value: number): string {
  if (value >= 1000) return value.toFixed(0);
  if (value >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

export function PriceChart({
  points,
  markers = [],
  title,
  subtitle,
  height = 340,
  accent = "#7dd3fc",
}: Props) {
  const usable = points.filter((point) => point.close !== null && point.close > 0);

  if (usable.length < 2) {
    return (
      <figure className="space-y-3">
        <figcaption>
          <h3>{title}</h3>
          {subtitle && <p className="text-base text-muted-foreground">{subtitle}</p>}
        </figcaption>
        <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
          No data yet. This security has fewer than two price bars.
        </p>
      </figure>
    );
  }

  const closes = usable.map((point) => point.close as number);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || Math.max(max * 0.02, 0.01);
  const low = min - span * 0.08;
  const high = max + span * 0.08;

  const plotWidth = WIDTH - PAD_LEFT - PAD_RIGHT;
  const plotHeight = height - PAD_TOP - PAD_BOTTOM;

  const xFor = (index: number) =>
    PAD_LEFT + (index / (usable.length - 1)) * plotWidth;
  const yFor = (value: number) =>
    PAD_TOP + plotHeight - ((value - low) / (high - low)) * plotHeight;

  const linePath = usable
    .map((point, index) => `${index === 0 ? "M" : "L"}${xFor(index).toFixed(2)},${yFor(point.close as number).toFixed(2)}`)
    .join(" ");

  const areaPath =
    `${linePath} L${xFor(usable.length - 1).toFixed(2)},${(PAD_TOP + plotHeight).toFixed(2)}` +
    ` L${xFor(0).toFixed(2)},${(PAD_TOP + plotHeight).toFixed(2)} Z`;

  const indexByDate = new Map(usable.map((point, index) => [point.date, index]));
  const nearestIndex = (date: string) => {
    const exact = indexByDate.get(date);
    if (exact !== undefined) return exact;
    let best = -1;
    for (let i = 0; i < usable.length; i += 1) {
      if (usable[i].date <= date) best = i;
      else break;
    }
    return best;
  };

  const gridValues = [0, 0.25, 0.5, 0.75, 1].map((fraction) => low + (high - low) * fraction);
  const tickCount = Math.min(6, usable.length);
  const xTicks = Array.from({ length: tickCount }, (_, i) =>
    Math.round((i / (tickCount - 1)) * (usable.length - 1)),
  );

  const gradientId = `grad-${title.replace(/[^a-z0-9]/gi, "")}`;

  return (
    <figure className="space-y-3">
      <figcaption>
        <h3>{title}</h3>
        {subtitle && <p className="text-base text-muted-foreground">{subtitle}</p>}
      </figcaption>
      <div className="overflow-x-auto rounded-xl border border-border bg-card p-4">
        <svg
          viewBox={`0 0 ${WIDTH} ${height}`}
          width="100%"
          height={height}
          role="img"
          aria-label={`${title}. ${usable.length} daily closes from ${usable[0].date} to ${usable[usable.length - 1].date}.`}
          style={{ display: "block", minWidth: 520 }}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={accent} stopOpacity="0.28" />
              <stop offset="100%" stopColor={accent} stopOpacity="0" />
            </linearGradient>
          </defs>

          {gridValues.map((value) => (
            <g key={value}>
              <line
                x1={PAD_LEFT}
                x2={WIDTH - PAD_RIGHT}
                y1={yFor(value)}
                y2={yFor(value)}
                stroke="currentColor"
                strokeOpacity="0.14"
                strokeWidth="1"
              />
              <text
                x={PAD_LEFT - 12}
                y={yFor(value) + 6}
                textAnchor="end"
                fill="currentColor"
                fillOpacity="0.75"
                fontSize="18"
                fontFamily="ui-monospace, monospace"
              >
                {formatPrice(value)}
              </text>
            </g>
          ))}

          {xTicks.map((index) => (
            <text
              key={index}
              x={xFor(index)}
              y={height - 16}
              textAnchor={index === 0 ? "start" : index === usable.length - 1 ? "end" : "middle"}
              fill="currentColor"
              fillOpacity="0.75"
              fontSize="17"
              fontFamily="ui-monospace, monospace"
            >
              {usable[index].date}
            </text>
          ))}

          <path d={areaPath} fill={`url(#${gradientId})`} />
          <path d={linePath} fill="none" stroke={accent} strokeWidth="2.2" strokeLinejoin="round" />

          {markers.map((marker) => {
            const index = nearestIndex(marker.date);
            if (index < 0) return null;
            const x = xFor(index);
            if (marker.kind === "dividend") {
              return (
                <line
                  key={`${marker.kind}-${marker.date}`}
                  x1={x}
                  x2={x}
                  y1={PAD_TOP + plotHeight - 8}
                  y2={PAD_TOP + plotHeight}
                  stroke="#a3e635"
                  strokeOpacity="0.85"
                  strokeWidth="2"
                />
              );
            }
            return (
              <g key={`${marker.kind}-${marker.date}`}>
                <line
                  x1={x}
                  x2={x}
                  y1={PAD_TOP}
                  y2={PAD_TOP + plotHeight}
                  stroke="#fbbf24"
                  strokeWidth="2"
                  strokeDasharray="6 4"
                />
                <text
                  x={x + 8}
                  y={PAD_TOP + 20}
                  fill="#fbbf24"
                  fontSize="18"
                  fontFamily="ui-monospace, monospace"
                >
                  {marker.label}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </figure>
  );
}
