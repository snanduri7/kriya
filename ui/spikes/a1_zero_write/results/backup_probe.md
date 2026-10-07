# Backup-API acquisition probe (MEASURED) - for the D-4 gate decision, not an implementation

- measured_at 2026-10-04T12:40:02, Python 3.14.6, SQLite 3.53.4

| case | acquisition | source-side changes (inventory) | snapshot inspection under deny-write | inspection denials | snapshot files changed |
|---|---|---|---|---|---|
| C02_rollback_hot_journal | REFUSED SQLITE_READONLY_ROLLBACK (25.0 ms; src journal -; dest journal -; quick_check -; rows -) | none | - | - | - |
| C03_wal_clean | OK (12.4 ms; src journal wal; dest journal delete; quick_check ok; rows 120) | traces.db-shm created; traces.db-wal created | READ_OK |  | none |
| C04_wal_sidecars_orphaned | OK (12.6 ms; src journal wal; dest journal delete; quick_check ok; rows 121) | traces.db-shm modified | READ_OK |  | none |
| C05_wal_active_writer | OK (14.0 ms; src journal wal; dest journal delete; quick_check ok; rows 121) | traces.db-shm modified | READ_OK |  | none |
| C09_missing_store | REFUSED SQLITE_CANTOPEN (0.1 ms; src journal -; dest journal -; quick_check -; rows -) | none | - | - | - |
