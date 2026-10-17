# Security policy

Security fixes target the latest released version. Upgrade before checking whether a report still reproduces.

## Reporting a vulnerability

Email leeguoooo@gmail.com with the affected version, platform, reproduction steps and expected impact. Do not publish credentials, personal data or exploit details in a public issue. Prefer a minimal reproduction using synthetic data. We will assess the report and coordinate fixes and disclosure; there is no guaranteed response time or bounty program.

## Boundaries

Notes are stored in the Git remote you choose. Review changes before committing; secret-pattern checks reduce accidental disclosure but cannot detect every secret. Store secret values in a vault and keep only references in notes. Private repository access and local known-secret lists must stay private.
