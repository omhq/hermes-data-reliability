# Incident storage

Share durable incident records between independent collection and investigation
processes. This Python 3.14 package imports only itself and the standard library.
It owns SQLite storage, source progress, matches, saved instruction payloads,
queue claims, and attempt accounting. Agent execution and report rendering
belong to the consuming application.

Use `incidents.database.connect(path)` to open schema version 2.
`incidents.queue.claim` commits an ownership token, lease, and attempt charge
before external work starts. `finish` requires that token and rejects an
expired or replaced claim. Release the transaction before starting model or
tool calls. The application coordinates chat and maintenance separately.

Store the database on persistent local storage and retain its journal after an
interruption. Package import paths do not change the schema or saved state.
See [architecture](../docs/ARCHITECTURE.md) for ownership and budget rules, and
[recovery](../docs/RECOVERY.md) for reboot and backup behavior.
