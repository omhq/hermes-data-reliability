# Incident storage

Stores incidents, collection progress, findings, and investigation attempts in
SQLite. The collector and investigator share this saved state.

Keeps pending investigations and attempt counts across restarts, coordinates
claims, and rejects results from expired attempts.
