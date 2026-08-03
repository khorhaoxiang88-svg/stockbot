import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getDebugTablesForSecurity } from "@/lib/db";

/**
 * Every stored row for one security, across every table, raw.
 *
 * This is deliberately NOT a curated view. Every other page in this app
 * formats, labels and interprets what it shows; this one exists so that state
 * can be inspected without reading the code that produced it, which means
 * showing exactly what is in each column, unrelabelled. A cell that looks
 * like NULL is NULL, not an empty string standing in for it.
 */

export const dynamic = "force-dynamic";

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "string" && value.length > 300) {
    return `${value.slice(0, 300)}… (${value.length} chars)`;
  }
  return String(value);
}

export default async function DebugSecurityPage({
  params,
}: {
  params: Promise<{ security_id: string }>;
}) {
  const { security_id: id } = await params;
  const securityId = Number(id);
  const result = Number.isInteger(securityId) && securityId > 0
    ? getDebugTablesForSecurity(securityId)
    : { status: { state: "error" as const, path: "", message: "invalid id" }, cik: null, tables: [] };

  const tablesWithRows = result.tables.filter((t) => t.rows.length > 0);
  const emptyTables = result.tables.filter((t) => t.rows.length === 0);

  return (
    <main className="mx-auto max-w-7xl px-6 py-12">
      <p className="mb-2 font-mono text-xs uppercase tracking-widest text-muted-foreground">
        <Link href="/health" className="underline underline-offset-4">
          stockbot
        </Link>{" "}
        / debug / {id}
      </p>
      <h1 className="mb-2">Debug: security {id}</h1>
      <p className="mb-8 text-muted-foreground">
        Every stored row for this security, across every table, unformatted.
        <Link href={`/security/${id}`} className="ml-2 underline underline-offset-4">
          Go to the formatted page →
        </Link>
      </p>

      {result.status.state !== "ok" ? (
        <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
          {result.status.state === "missing"
            ? "No data yet. The database does not exist."
            : `Error: ${"message" in result.status ? result.status.message : "unknown"}`}
        </p>
      ) : (
        <>
          <div className="mb-8 flex flex-wrap items-center gap-3">
            <Badge variant="outline" className="px-3 py-1 font-mono">
              CIK {result.cik ?? "none"}
            </Badge>
            <Badge variant="outline" className="px-3 py-1 font-mono">
              {tablesWithRows.length} table(s) with rows
            </Badge>
            <Badge variant="outline" className="px-3 py-1 font-mono">
              {emptyTables.length} table(s) empty for this security
            </Badge>
          </div>

          {tablesWithRows.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
              No data yet. Every queried table has zero rows for this security_id.
            </p>
          ) : (
            <div className="space-y-12">
              {tablesWithRows.map((table) => (
                <section key={table.table} id={table.table} className="space-y-3">
                  <div className="flex flex-wrap items-baseline gap-3">
                    <h2 className="text-lg font-semibold">{table.label}</h2>
                    <span className="font-mono text-xs text-muted-foreground">
                      {table.table}
                    </span>
                    <Badge variant="outline" className="px-2 py-0.5 font-mono text-xs">
                      {table.rows.length} of {table.totalCount} row
                      {table.totalCount === 1 ? "" : "s"}
                      {table.truncated ? " (capped)" : ""}
                    </Badge>
                  </div>
                  {table.truncated ? (
                    <p className="text-xs text-amber-200">
                      Showing the first {table.rows.length} of {table.totalCount} rows. Not
                      silently: {table.totalCount - table.rows.length} more row(s) exist and
                      are not rendered here.
                    </p>
                  ) : null}
                  <div className="overflow-x-auto rounded-lg border border-border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          {table.columns.map((column) => (
                            <TableHead key={column} className="font-mono text-xs">
                              {column}
                            </TableHead>
                          ))}
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {table.rows.map((row, index) => (
                          <TableRow key={index}>
                            {table.columns.map((column) => (
                              <TableCell
                                key={column}
                                className="max-w-xs truncate font-mono text-xs"
                                title={cellText(row[column])}
                              >
                                {cellText(row[column])}
                              </TableCell>
                            ))}
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </section>
              ))}
            </div>
          )}

          {emptyTables.length ? (
            <details className="mt-12 rounded-lg border border-border p-4">
              <summary className="cursor-pointer font-medium">
                {emptyTables.length} table(s) with zero rows for this security
              </summary>
              <ul className="mt-3 grid gap-1 font-mono text-sm text-muted-foreground">
                {emptyTables.map((table) => (
                  <li key={table.table}>
                    {table.table} — {table.label}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      )}
    </main>
  );
}
