"""Shared security primitives for BigQuery Optimization Control Plane.

Pure standard-library module (no GCP SDK or Flask imports) used across the
web application, CLI, rules engine, verifier, and executors to enforce:
  * Strict BigQuery / GCP identifier & URI validation (blocking SQL/script injection)
  * Lexer-aware GoogleSQL single-statement, read-only SELECT, @param & table-ref checks
  * SQL PII & secret scrubbing before LLM prompts or telemetry persistence
  * Outbound webhook URL + DNS/IP SSRF protection (blocking RFC1918, loopback & Cloud Metadata)
  * Slack mrkdwn escaping, log-injection sanitization, and path-traversal guards
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Identifier, FQN & URI validation (strict \Z full-string anchors)
# ---------------------------------------------------------------------------

_PROJECT_RE = re.compile(r"^[a-z][a-z0-9.:-]{0,62}\Z")
_PROJECT_STRICT_RE = re.compile(r"^[a-z][-a-z0-9:.]{4,62}[a-z0-9]\Z")
_DATASET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}\Z")
_TABLE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-$]{0,1023}\Z")
_REGION_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}\Z")
_RES_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_RLS_POLICY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,255}\Z")
_GRANTEE_RE = re.compile(
    r"^(?:allAuthenticatedUsers|(?:user|serviceAccount|group|domain|principal|principalSet):[A-Za-z0-9._%+\-@/:]+)\Z"
)
_GCS_URI_RE = re.compile(r"^gs://([a-z0-9][a-z0-9._-]{1,61}[a-z0-9])/([A-Za-z0-9._/\-*]+)\Z")
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\Z")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_JOBS_VIEWS = frozenset({"JOBS", "JOBS_BY_PROJECT", "JOBS_BY_USER", "JOBS_BY_FOLDER", "JOBS_BY_ORGANIZATION"})
_RECS_VIEWS = frozenset({"RECOMMENDATIONS", "RECOMMENDATIONS_BY_PROJECT", "RECOMMENDATIONS_BY_ORGANIZATION"})
_PARTITION_GRANULARITIES = frozenset({"HOUR", "DAY", "MONTH", "YEAR"})

DEFAULT_ALLOWED_WEBHOOK_HOSTS = (
    "hooks.slack.com",
    "hooks.slack-gov.com",
    "chat.googleapis.com",
    "webhook.office.com",
)


class SecurityValidationError(ValueError):
    """Raised when an identifier, SQL statement, URL, or path fails security validation."""


def validate_project_id(project_id: Any, *, strict_gcp_len: bool = False) -> str:
    s = str(project_id or "").strip().strip("`")
    rx = _PROJECT_STRICT_RE if strict_gcp_len else _PROJECT_RE
    if not s or ".." in s or "::" in s or s.endswith((".", "-", ":")) or not rx.fullmatch(s):
        raise SecurityValidationError(f"Invalid GCP project_id: {project_id!r}")
    return s


def validate_dataset_id(dataset_id: Any) -> str:
    s = str(dataset_id or "").strip().strip("`")
    if not s or not _DATASET_RE.fullmatch(s):
        raise SecurityValidationError(f"Invalid BigQuery dataset_id: {dataset_id!r}")
    return s


def validate_table_id(table_id: Any) -> str:
    s = str(table_id or "").strip().strip("`")
    if not s or ".." in s or not _TABLE_RE.fullmatch(s):
        raise SecurityValidationError(f"Invalid BigQuery table_id: {table_id!r}")
    return s


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}\Z")
_COL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,299}\Z")


def validate_bq_identifier(value: Any, *, kind: str = "identifier") -> str:
    """Validate a BigQuery dataset, table, or column identifier strictly against SQL injection."""
    if kind in ("dataset", "target_dataset", "ops_dataset"):
        return validate_dataset_id(value)
    if kind in ("table", "target_table"):
        return validate_table_id(value)
    s = str(value or "").strip().strip("`")
    if not s or not _COL_RE.fullmatch(s):
        raise SecurityValidationError(f"Invalid BigQuery {kind}: {value!r}")
    return s


def validate_email(email: Any) -> str:
    """Validate and normalize a human or service-account email address."""
    s = str(email or "").strip().lower()
    if not s or len(s) > 254 or ".." in s or not _EMAIL_RE.fullmatch(s):
        raise SecurityValidationError(f"Invalid email address: {email!r}")
    return s


def validate_region(region: Any) -> str:
    s = str(region or "").strip().lower()
    if s.startswith("region-"):
        s = s[len("region-"):]
    if not s or not _REGION_RE.fullmatch(s):
        raise SecurityValidationError(f"Invalid GCP/BigQuery region: {region!r}")
    return s


def validate_jobs_view(view: Any) -> str:
    s = str(view or "JOBS").strip().upper()
    if s not in _JOBS_VIEWS:
        raise SecurityValidationError(f"Unsupported INFORMATION_SCHEMA jobs view: {view!r}")
    return s


def validate_recs_view(view: Any) -> str:
    s = str(view or "RECOMMENDATIONS").strip().upper()
    if s not in _RECS_VIEWS:
        raise SecurityValidationError(f"Unsupported INFORMATION_SCHEMA recommendations view: {view!r}")
    return s


def validate_partition_granularity(granularity: Any) -> str:
    s = str(granularity or "DAY").strip().upper()
    if s not in _PARTITION_GRANULARITIES:
        raise SecurityValidationError(f"Unsupported partition granularity: {granularity!r}")
    return s


def quote_ident(name: Any) -> str:
    """Safely backtick-quote a single BigQuery identifier (column, table, dataset)."""
    if not isinstance(name, str):
        raise SecurityValidationError(f"Identifier must be a string, got {type(name).__name__}")
    s = name.strip()
    if not s or len(s) > 1024 or any(ch in s for ch in ("`", "\x00", "\r", "\n", ";")):
        raise SecurityValidationError(f"Unsafe BigQuery identifier: {name!r}")
    return f"`{s}`"


def validate_column_name(col: Any, allowed_columns: Iterable[str] | None = None) -> str:
    if not isinstance(col, str):
        raise SecurityValidationError(f"Column name must be a string, got {type(col).__name__}")
    s = col.strip()
    if not s or len(s) > 300 or any(ch in s for ch in ("`", "\x00", "\r", "\n", ";")):
        raise SecurityValidationError(f"Unsafe column name: {col!r}")
    if allowed_columns is not None and s not in set(allowed_columns):
        raise SecurityValidationError(f"Column {s!r} does not exist on target table schema")
    return s


def validate_fqn(fqn: Any) -> tuple[str, str, str]:
    """Validate 'project.dataset.table' (supporting domain-scoped 'domain.com:proj.ds.tbl')."""
    s = str(fqn or "").strip().strip("`")
    parts = s.rsplit(".", 2)
    if len(parts) != 3:
        raise SecurityValidationError(f"Expected 'project.dataset.table', got {fqn!r}")
    return validate_project_id(parts[0]), validate_dataset_id(parts[1]), validate_table_id(parts[2])


def quote_fqn(project_id: Any, dataset_id: Any, table_id: Any) -> str:
    p = validate_project_id(project_id)
    d = validate_dataset_id(dataset_id)
    t = validate_table_id(table_id)
    return f"`{p}.{d}.{t}`"


def validate_reservation_fq(fq: Any) -> str:
    """Validate 'project.region-<loc>.<reservation_name>'."""
    s = str(fq or "").strip().strip("`")
    parts = s.rsplit(".", 2)
    if len(parts) != 3 or not parts[1].startswith("region-"):
        raise SecurityValidationError(f"Invalid reservation identifier: {fq!r}")
    p = validate_project_id(parts[0])
    reg = validate_region(parts[1])
    name = parts[2]
    if not _RES_NAME_RE.fullmatch(name):
        raise SecurityValidationError(f"Invalid reservation name: {name!r}")
    return f"{p}.region-{reg}.{name}"


def validate_assignment_fq(fq: Any) -> str:
    """Validate 'project.region-<loc>.<reservation_name>.<assignment_id>'."""
    s = str(fq or "").strip().strip("`")
    parts = s.rsplit(".", 3)
    if len(parts) != 4 or not parts[1].startswith("region-"):
        raise SecurityValidationError(f"Invalid assignment identifier: {fq!r}")
    p = validate_project_id(parts[0])
    reg = validate_region(parts[1])
    res_name, assign_id = parts[2], parts[3]
    if not _RES_NAME_RE.fullmatch(res_name) or not _RES_NAME_RE.fullmatch(assign_id):
        raise SecurityValidationError(f"Invalid reservation/assignment name: {fq!r}")
    return f"{p}.region-{reg}.{res_name}.{assign_id}"


def validate_rls_policy_id(policy_id: Any) -> str:
    s = str(policy_id or "").strip()
    if not s or not _RLS_POLICY_RE.fullmatch(s):
        raise SecurityValidationError(f"Invalid row access policy_id: {policy_id!r}")
    return s


def validate_iam_grantee(grantee: Any) -> str:
    s = str(grantee or "").strip()
    if not s or not _GRANTEE_RE.fullmatch(s):
        raise SecurityValidationError(f"Invalid IAM grantee principal: {grantee!r}")
    return s


def validate_gcs_uri(uri: Any) -> str:
    s = str(uri or "").strip()
    m = _GCS_URI_RE.fullmatch(s)
    if not m:
        raise SecurityValidationError(f"Invalid GCS URI: {uri!r}")
    path_part = m.group(2)
    if ".." in path_part.split("/") or "//" in path_part:
        raise SecurityValidationError(f"Unsafe path segment in GCS URI: {uri!r}")
    return s


# ---------------------------------------------------------------------------
# Path, slug, Slack mrkdwn & log sanitization
# ---------------------------------------------------------------------------

def safe_slug(value: Any, *, fallback: str = "query", max_len: int = 80) -> str:
    """Safe filename/git-branch slug: lowercase [a-z0-9_-] only (dots forbidden -> no '..')."""
    raw = str(value or "").lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
    if max_len > 0:
        slug = slug[:max_len].strip("-_")
    return slug or fallback


def ensure_relative_subpath(base_dir: str, rel_path: str) -> str:
    base_abs = os.path.realpath(base_dir)
    cand_abs = os.path.realpath(os.path.join(base_abs, rel_path))
    if cand_abs == base_abs or not cand_abs.startswith(base_abs + os.sep):
        raise SecurityValidationError(f"Relative path escapes base directory: {rel_path!r}")
    return rel_path


def escape_slack_mrkdwn(text: Any, *, max_len: int = 500) -> str:
    """Neutralize Slack mrkdwn control tokens (<!channel>, <!everyone>, <http://...|spoof>)."""
    s = str(text if text is not None else "")
    s = _ANSI_RE.sub("", s).replace("\x00", "").replace("\r", " ")
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if max_len > 0 and len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def sanitize_log_value(val: Any, *, max_len: int = 300) -> str:
    """Single-line, ANSI-free string safe for structured or line-oriented logs."""
    s = str(val if val is not None else "")
    s = _ANSI_RE.sub("", s)
    s = "".join(" " if (ord(ch) < 32 or ord(ch) == 127) else ch for ch in s)
    s = re.sub(r"\s+", " ", s).strip()
    if max_len > 0 and len(s) > max_len:
        s = s[:max_len] + "…"
    return s


# ---------------------------------------------------------------------------
# URL & Outbound Webhook SSRF defenses
# ---------------------------------------------------------------------------

def validate_http_url(url: Any, *, max_len: int = 512, require_https: bool = False) -> str:
    s = str(url or "").strip()
    if not s:
        raise SecurityValidationError("URL cannot be empty")
    if len(s) > max_len:
        raise SecurityValidationError(f"URL exceeds maximum length ({max_len} chars)")
    if any(ord(ch) <= 32 or ord(ch) == 127 for ch in s):
        raise SecurityValidationError("URL contains whitespace or control characters")
    parsed = urllib.parse.urlsplit(s)
    allowed_schemes = ("https",) if require_https else ("http", "https")
    if parsed.scheme.lower() not in allowed_schemes:
        raise SecurityValidationError(
            f"URL must use {'https://' if require_https else 'http:// or https://'}"
        )
    if parsed.username or parsed.password:
        raise SecurityValidationError("URL must not embed credentials")
    host = (parsed.hostname or "").strip().lower()
    if not host or (not _HOST_RE.fullmatch(host) and ":" not in host):
        raise SecurityValidationError("URL hostname is invalid")
    return s


_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


def is_forbidden_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NET:
        return True
    return False


def _host_matches_allowlist(host: str, allowed_hosts: Iterable[str]) -> bool:
    h = host.lower().rstrip(".")
    for pat in allowed_hosts:
        p = pat.strip().lower().rstrip(".")
        if not p:
            continue
        if p.startswith("*."):
            suffix = p[1:]  # '.webhook.office.com'
            if h.endswith(suffix) and len(h) > len(suffix):
                return True
        elif h == p:
            return True
    return False


def validate_webhook_url(
    url: Any,
    *,
    allowed_hosts: Iterable[str] | None = None,
    resolve_dns: bool = True,
) -> str:
    """Validate an outbound Slack/Teams/Google Chat webhook URL and block SSRF targets."""
    s = validate_http_url(url, max_len=1024, require_https=True)
    parsed = urllib.parse.urlsplit(s)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in ("localhost", "metadata", "metadata.google.internal") or host.endswith((".internal", ".local")):
        raise SecurityValidationError(f"Webhook host {host!r} is forbidden")
    try:
        ipaddress.ip_address(host)
        raise SecurityValidationError("Webhook URL must use a verified DNS hostname, not a raw IP literal")
    except ValueError:
        pass

    if allowed_hosts is None:
        env_extra = [x.strip() for x in os.environ.get("BQOPT_ALLOWED_WEBHOOK_HOSTS", "").split(",") if x.strip()]
        allowed = list(DEFAULT_ALLOWED_WEBHOOK_HOSTS) + ["*.webhook.office.com", "*.logic.azure.com"] + env_extra
    else:
        allowed = list(allowed_hosts)

    if allowed and not _host_matches_allowlist(host, allowed):
        raise SecurityValidationError(f"Webhook host {host!r} is not in the allowed webhook host list")

    if resolve_dns:
        port = parsed.port or 443
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise SecurityValidationError(f"Could not resolve webhook host {host!r}: {e}") from e
        if not infos:
            raise SecurityValidationError(f"Webhook host {host!r} resolved to no addresses")
        for info in infos:
            ip_str = info[4][0]
            if is_forbidden_ip(ip_str):
                raise SecurityValidationError(f"Webhook host {host!r} resolved to forbidden IP {ip_str}")
    return s


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "HTTP redirects disabled for security", headers, fp)


def safe_urlopen(req: urllib.request.Request, *, timeout: float = 3.0):
    """Open an outbound HTTPS request with HTTP redirects strictly disabled."""
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(req, timeout=timeout)


# ---------------------------------------------------------------------------
# GoogleSQL lexical scanner, single-statement & read-only SELECT guards, PII scrubber
# ---------------------------------------------------------------------------

def _scan_sql_segments(sql: str) -> list[tuple[str, str]]:
    """Tokenize GoogleSQL into ('code' | 'string' | 'ident' | 'comment', text) segments."""
    out: list[tuple[str, str]] = []
    n = len(sql)
    i = 0
    code_start = 0

    def flush_code(end: int) -> None:
        if end > code_start:
            out.append(("code", sql[code_start:end]))

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        # Single-line -- or # comment
        if (ch == "-" and nxt == "-") or ch == "#":
            flush_code(i)
            j = sql.find("\n", i)
            end = n if j == -1 else j + 1
            out.append(("comment", sql[i:end]))
            i = code_start = end
            continue

        # Multi-line /* ... */ comment
        if ch == "/" and nxt == "*":
            flush_code(i)
            j = sql.find("*/", i + 2)
            end = n if j == -1 else j + 2
            out.append(("comment", sql[i:end]))
            i = code_start = end
            continue

        # Raw/bytes string prefix r'...', b'...', rb'...'
        if ch in ("r", "R", "b", "B") and i + 1 < n:
            p_end = i + 1
            if p_end < n and sql[p_end] in ("r", "R", "b", "B") and sql[p_end].lower() != ch.lower():
                p_end += 1
            if p_end < n and sql[p_end] in ("'", '"'):
                flush_code(i)
                q = sql[p_end]
                triple = sql[p_end:p_end + 3] == q * 3
                delim = q * 3 if triple else q
                j = p_end + len(delim)
                while j < n:
                    if sql[j] == "\\":
                        j += 2
                        continue
                    if sql.startswith(delim, j):
                        j += len(delim)
                        break
                    j += 1
                out.append(("string", sql[i:j]))
                i = code_start = j
                continue

        # Quoted string literal '...' or "..." (including triple-quoted)
        if ch in ("'", '"'):
            flush_code(i)
            triple = sql[i:i + 3] == ch * 3
            delim = ch * 3 if triple else ch
            j = i + len(delim)
            while j < n:
                if sql[j] == "\\":
                    j += 2
                    continue
                if not triple and sql[j] == ch and j + 1 < n and sql[j + 1] == ch:
                    j += 2
                    continue
                if sql.startswith(delim, j):
                    j += len(delim)
                    break
                j += 1
            out.append(("string", sql[i:j]))
            i = code_start = j
            continue

        # Backtick identifier `...`
        if ch == "`":
            flush_code(i)
            j = i + 1
            while j < n:
                if sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == "`":
                    j += 1
                    break
                j += 1
            out.append(("ident", sql[i:j]))
            i = code_start = j
            continue

        i += 1

    flush_code(n)
    return out


def strip_sql_comments_and_literals(sql: str) -> str:
    """Replace comments with spaces and string literals with '' while preserving code & `identifiers`."""
    parts: list[str] = []
    for kind, text in _scan_sql_segments(sql or ""):
        if kind == "comment":
            parts.append("\n" if "\n" in text else " ")
        elif kind == "string":
            parts.append("''")
        else:
            parts.append(text)
    return "".join(parts)


_SQL_PARAM_RE = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)")


def replace_sql_params_outside_literals(sql: str, replacement: str = "'1'") -> str:
    """Replace @query_param tokens ONLY in SQL code segments (never inside string literals or comments)."""
    parts: list[str] = []
    for kind, text in _scan_sql_segments(sql or ""):
        if kind == "code":
            parts.append(_SQL_PARAM_RE.sub(replacement, text))
        else:
            parts.append(text)
    return "".join(parts)


def assert_single_sql_statement(sql: str) -> str:
    """Ensure `sql` contains a single statement (no ';' except an optional trailing ';')."""
    s = (sql or "").strip()
    if not s:
        raise SecurityValidationError("SQL statement is empty")
    stripped = strip_sql_comments_and_literals(s).strip()
    if not stripped:
        raise SecurityValidationError("SQL statement contains only comments")
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    if ";" in stripped:
        raise SecurityValidationError("Multi-statement SQL scripts (';') are forbidden in this context")
    return s


_FORBIDDEN_READONLY_KEYWORDS = frozenset({
    "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "CREATE", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "CALL", "EXECUTE", "IMMEDIATE", "EXPORT", "LOAD", "ASSERT",
    "BEGIN", "COMMIT", "ROLLBACK", "EXTERNAL_QUERY", "SESSION_USER", "DECLARE", "SET",
})

_TABLE_CLAUSE_SCAN_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+"
    r"(?:`?([A-Za-z0-9_:-]+)`?\.)?"
    r"`?([A-Za-z0-9_]+)`?\."
    r"`?([A-Za-z0-9_$\-]+)`?",
    re.IGNORECASE,
)


def extract_sql_table_refs(sql: str, *, default_project: str | None = None) -> list[tuple[str | None, str, str]]:
    """Extract (project_or_None, dataset, table) from active SQL code only (ignoring comments & strings)."""
    clean = strip_sql_comments_and_literals(sql or "")
    ctes = {m.lower() for m in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", clean, re.IGNORECASE)}
    refs: list[tuple[str | None, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for m in _TABLE_CLAUSE_SCAN_RE.finditer(clean):
        proj, ds, tbl = m.group(1), m.group(2), m.group(3)
        if not proj and ds.lower() in ctes:
            continue
        if ds.upper() == "UNNEST":
            continue
        resolved_p = proj or default_project
        key = ((resolved_p or "").lower(), ds.lower(), tbl.lower())
        if key not in seen:
            seen.add(key)
            refs.append((resolved_p, ds, tbl))
    return refs


def validate_readonly_select_sql(
    sql: str,
    *,
    allowed_tables: set[tuple[str, str, str]] | None = None,
    default_project: str | None = None,
) -> str:
    """Verify `sql` is a single read-only SELECT/WITH query referencing only `allowed_tables`."""
    s = assert_single_sql_statement(sql)
    clean = strip_sql_comments_and_literals(s).strip()
    if clean.endswith(";"):
        clean = clean[:-1].strip()
    # Strip backtick identifiers before scanning for forbidden statement keywords
    code_only = re.sub(r"`[^`]*`", " ident ", clean)
    words = [w.upper() for w in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", code_only)]
    if not words or words[0] not in ("SELECT", "WITH"):
        raise SecurityValidationError("Query must be a single read-only SELECT or WITH statement")
    bad = [w for w in words if w in _FORBIDDEN_READONLY_KEYWORDS]
    if bad:
        raise SecurityValidationError(f"Query contains disallowed keyword(s): {', '.join(sorted(set(bad)))}")
    refs = extract_sql_table_refs(s, default_project=default_project)
    for proj, ds, tbl in refs:
        if ds.upper() == "INFORMATION_SCHEMA":
            raise SecurityValidationError("Query may not reference INFORMATION_SCHEMA")
        if allowed_tables is not None:
            norm_allowed = {(p.lower(), d.lower(), t.lower()) for p, d, t in allowed_tables}
            cand = ((proj or "").lower(), ds.lower(), tbl.lower())
            if cand not in norm_allowed:
                raise SecurityValidationError(
                    f"Query references unauthorized table {proj or '?'}.{ds}.{tbl}"
                )
    return s


_EMAIL_PII_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_SSN_PII_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_SECRET_TOKEN_RE = re.compile(
    r"(?:ya29\.[A-Za-z0-9._\-]+|AIza[0-9A-Za-z_\-]{35}|Bearer\s+[A-Za-z0-9._\-+/=]{12,})",
    re.IGNORECASE,
)
_PAN_DIGITS_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _scrub_text_piece(piece: str) -> str:
    piece = _SECRET_TOKEN_RE.sub("<REDACTED_SECRET>", piece)
    piece = _EMAIL_PII_RE.sub("<REDACTED_EMAIL>", piece)
    piece = _SSN_PII_RE.sub("<REDACTED_SSN>", piece)
    piece = _PAN_DIGITS_RE.sub("<REDACTED_ID>", piece)
    return piece


def scrub_sql_pii(sql: str | None) -> str:
    """Redact literal emails, SSNs, card/account numbers, and tokens inside SQL literals & comments."""
    if not sql:
        return ""
    parts: list[str] = []
    for kind, text in _scan_sql_segments(sql):
        if kind in ("string", "comment"):
            parts.append(_scrub_text_piece(text))
        else:
            parts.append(_SECRET_TOKEN_RE.sub("<REDACTED_SECRET>", text))
    return "".join(parts)


_DDL_PATTERNS: dict[str, re.Pattern[str]] = {
    "ALTER_TABLE_CONSTRAINT": re.compile(
        r"^ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:`[^`]+`|[A-Za-z0-9_.\-:]+)\s+"
        r"ADD\s+(?:CONSTRAINT\s+(?:`[^`]+`|[A-Za-z0-9_]+)\s+)?"
        r"(?:PRIMARY\s+KEY|FOREIGN\s+KEY)\b",
        re.IGNORECASE,
    ),
    "ALTER_TABLE": re.compile(
        r"^ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:`[^`]+`|[A-Za-z0-9_.\-:]+)\s+",
        re.IGNORECASE,
    ),
    "ALTER_RESERVATION": re.compile(
        r"^ALTER\s+RESERVATION\s+(?:`[^`]+`|[A-Za-z0-9_.\-:]+)\s+SET\s+OPTIONS\s*\(",
        re.IGNORECASE,
    ),
    "CREATE_MATERIALIZED_VIEW": re.compile(
        r"^CREATE\s+(?:OR\s+REPLACE\s+)?MATERIALIZED\s+VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:`[^`]+`|[A-Za-z0-9_.\-:]+)\b",
        re.IGNORECASE,
    ),
    "CREATE_SEARCH_INDEX": re.compile(
        r"^CREATE\s+(?:OR\s+REPLACE\s+)?SEARCH\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:(?:`[^`]+`|[A-Za-z0-9_]+)\s+)?ON\s+(?:`[^`]+`|[A-Za-z0-9_.\-:]+)\s*\(",
        re.IGNORECASE,
    ),
    "DROP_MATERIALIZED_VIEW": re.compile(
        r"^DROP\s+MATERIALIZED\s+VIEW\s+(?:IF\s+EXISTS\s+)?(?:`[^`]+`|[A-Za-z0-9_.\-:]+)\s*\Z",
        re.IGNORECASE,
    ),
    "DROP_SEARCH_INDEX": re.compile(
        r"^DROP\s+SEARCH\s+INDEX\s+(?:IF\s+EXISTS\s+)?(?:(?:`[^`]+`|[A-Za-z0-9_]+)\s+)?"
        r"ON\s+(?:`[^`]+`|[A-Za-z0-9_.\-:]+)\s*\Z",
        re.IGNORECASE,
    ),
    "CREATE_ROW_ACCESS_POLICY": re.compile(
        r"^CREATE\s+(?:OR\s+REPLACE\s+)?ROW\s+ACCESS\s+POLICY\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:`[^`]+`|[A-Za-z0-9_\-]+)\s+ON\s+(?:`[^`]+`|[A-Za-z0-9_.\-:]+)\s+"
        r"GRANT\s+TO\s*\([^)]+\)\s+FILTER\s+USING\s*\(.+\)\s*\Z",
        re.IGNORECASE | re.DOTALL,
    ),
}


def assert_ddl_statement(sql: str, allowed_types: Iterable[str]) -> str:
    """Verify `sql` is a single statement matching one of `allowed_types` with no dangerous clauses."""
    s = assert_single_sql_statement(sql)
    clean = strip_sql_comments_and_literals(s).strip()
    if clean.endswith(";"):
        clean = clean[:-1].strip()
    matched = False
    for kind in allowed_types:
        pat = _DDL_PATTERNS.get(kind)
        if pat and pat.search(clean):
            matched = True
            break
    if not matched:
        raise SecurityValidationError(
            f"SQL statement does not match allowed DDL type(s): {', '.join(allowed_types)}"
        )
    code_only = re.sub(r"`[^`]*`", " ident ", clean)
    upper_words = {w.upper() for w in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", code_only)}
    forbidden_in_ddl = {"EXECUTE", "IMMEDIATE", "EXTERNAL_QUERY", "EXPORT", "LOAD", "GRANT", "REVOKE", "CALL"}
    if "CREATE_ROW_ACCESS_POLICY" in set(allowed_types):
        forbidden_in_ddl = forbidden_in_ddl - {"GRANT"}
    bad = sorted(upper_words & forbidden_in_ddl)
    if bad:
        raise SecurityValidationError(f"DDL contains forbidden keyword(s): {', '.join(bad)}")
    if re.search(r"\bINFORMATION_SCHEMA\b", code_only, re.IGNORECASE):
        raise SecurityValidationError("Generated DDL may not reference INFORMATION_SCHEMA")
    return s
