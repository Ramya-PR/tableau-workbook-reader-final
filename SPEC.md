# Design notes

## Goal
Given a Tableau workbook from Tableau Public, Tableau Server/Cloud, or a local file, show its metadata and (when bundled) its data as tables, to support inventory ahead of a migration to another BI tool.

## Division of labour
- **TSC** lists and downloads workbooks from Server/Cloud. It does not parse them.
- **lxml** parses the workbook XML (`.twb`, or the `.twb` inside a `.twbx`).
- **Hyper API** (optional) reads rows from bundled `.hyper` extracts.
- **FastAPI** exposes this over a small JSON API; **React** renders it.

## Flow
1. A `WorkbookSource` (upload, public, server) puts a file in a private session folder.
2. `WorkbookArchive` validates it and reads the workbook XML and the list of data files.
3. `parse_workbook` returns fields, worksheets, dashboards, datasources and warnings.
4. The UI shows metadata immediately; row previews are fetched page by page on demand.

## Parser output
- **Fields:** physical, calculated, parameter, bin, group, set. Formulas have internal names (`Calculation_123`) replaced by captions.
- **Worksheets:** datasources, shelves in readable form, filters, mark types, dashboards they appear on.
- **Fields by worksheet:** each field a sheet uses with its aggregation or date part.
- **Dashboards:** worksheets and other objects.
- **Datasources:** mode (extract, live, file, published, parameters), connector, server, database, custom SQL, joins, bundled data file. Credentials are never emitted.

## Migration assessment
`migration.py` runs after parsing. It strips comments, string literals and `[field names]` from each formula, then applies keyword rules (LOD, table calculations, external scripts, raw SQL, user-based security, spatial, ATTR, regex, date functions, aggregations). Datasources are flagged for custom SQL, published datasources, extracts and joins. Each item gets a level; the workbook gets an overall level from a simple count rule (no weights). Quick table calculations set on a worksheet shelf are detected separately from formulas, and joins and relationships are listed with their keys. Tests cover each rule, false positives (strings, comments, field names), and mutation checks showing the cleaning step matters.

## Edge cases handled
- `.twb` vs `.twbx` detected by content, not extension.
- Zip bombs, path traversal, symlinks, oversized members.
- XXE and entity expansion in workbook XML; XML namespace prefixes that vary by version.
- Workbooks with only live connections, only published datasources, or no data files.
- Multiple extracts and tables in one workbook; CSV encoding and delimiter sniffing.
- Server errors (bad token, wrong site, permissions, timeouts, non-Tableau responses) mapped to plain messages.
- Large downloads streamed to disk; sessions expire and are cleaned up.

## Not handled
Tableau Prep flows, `.tds`/`.tdsx` datasource files (rejected with a clear message), `.tde` and legacy `.xls` row previews, rendering the visualisations themselves.

## Production security and privacy roadmap

The app is a single-user, local-first tool. The protections listed under Security notes in the README cover hostile files and credential leakage. Running it for a team would need the following, roughly in priority order. None of this is built yet.

**Before anyone else uses it**
1. **Authentication and authorization:** single sign-on (OIDC or SAML) and role checks. Today anyone who can reach the URL can use it.
2. **Per-user Tableau access:** the app currently uses one shared token. Use per-user tokens or Tableau connected apps so people only see workbooks they can already open.
3. **TLS and network controls:** HTTPS in front of the app, CORS limited to the real origin, internal network or VPN only.
4. **Secrets management:** move the token from `.env` to a vault, with rotation and a least-privilege service account.
5. **Audit logging:** record who opened which workbook and when, without logging contents.

**Protecting the data itself**
6. **Row preview controls:** make it opt-in per role, with a setting to turn it off, column masking for PII patterns (email, phone, SSN), and row caps. Metadata alone is usually enough for a migration inventory.
7. **Encrypted temp storage** and shorter retention.
8. **Metadata access:** server names, databases and custom SQL reveal internal architecture, so restrict them like the workbook.
9. **Data residency:** the app makes no calls except to the configured Tableau host; keep it that way and document it.

**Operations**
10. Rate limits, upload quotas and antivirus scanning of uploads.
11. Run as a non-root container with a read-only filesystem.
12. Dependency scanning (`pip-audit`, `npm audit`, Dependabot) in CI.
13. Shared, encrypted session storage for more than one server instance.
14. Security headers (CSP, `X-Content-Type-Options`) on the frontend.
