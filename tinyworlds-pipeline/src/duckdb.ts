/**
 * Load a DuckDB extension. Prefer LOAD of a copy already on disk so a flaky
 * extensions.duckdb.org (which serves INSTALL over HTTP) does not fail the run.
 */
export async function loadExtension(
  conn: { run: (sql: string) => Promise<unknown> },
  name: string,
): Promise<void> {
  try {
    await conn.run(`LOAD ${name}`);
  } catch {
    await conn.run(`INSTALL ${name}`);
    await conn.run(`LOAD ${name}`);
  }
}

export async function loadExtensions(
  conn: { run: (sql: string) => Promise<unknown> },
  ...names: string[]
): Promise<void> {
  for (const name of names) await loadExtension(conn, name);
}
