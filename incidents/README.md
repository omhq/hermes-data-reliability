# Incident storage

Stores incidents published by consumers, their investigation instructions,
findings, and investigation attempts in SQLite. The central investigator reads
pending incidents from this database.

Keeps pending investigations and attempt counts across restarts, coordinates
claims, and rejects results from expired attempts.
