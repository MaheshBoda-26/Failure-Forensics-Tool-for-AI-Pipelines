"""Storage — per-trace JSON payloads plus a SQLite index and relational tables.

Split rationale: full span payloads live in git-friendly, inspectable JSON
files; SQLite holds the index and the relational tables for flags, diagnoses,
eval cases, and eval runs. Owns atomic writes and the consistency story between
file and index.

Status: not implemented. See PROMPT.md C6.
"""
