# Security and Data Handling

This repository is a local portfolio prototype. It has not been assessed or configured for real PHI.
Use de-identified synthetic data only.

## Runtime behavior

- Uploaded bytes are processed within one request and are not intentionally persisted.
- OCR, Poppler, and the CMS grouper run as local subprocesses.
- Temporary directories use restrictive permissions and are removed after success, failure, or timeout.
- Application logs contain request IDs, hashes, counts, versions, timings, and sanitized error categories;
  they must not contain filenames, OCR text, MDS content, quotes, or resident identifiers.
- The Docker image runs as a non-root user and application/CMS runtime files are read-only.

## Explicitly absent

Authentication, authorization, encryption-at-rest controls, tenancy, audit retention, backups, incident
response integrations, production monitoring, a BAA posture, and validated PHI controls are out of scope.

Report security issues privately through GitHub's security-advisory feature. Do not include PHI or other
sensitive data in a report.
