# M8 local security scan

## Final image findings

Trivy 0.69.3 checked the fully rebuilt API and web images with HIGH and CRITICAL severity enabled. The backend contains 53 HIGH Debian package findings and zero CRITICAL findings. The web image contains zero HIGH/CRITICAL findings. Each CVE-package pair is counted separately. Trivy reported no fixed Debian version for the rows below.

| Severity | Backend Debian binary package | Installed | Advisories |
|---|---|---|---|
| HIGH | `bsdutils` | `1:2.41.5-0+deb13u1` | [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642), [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408), [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409), [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) |
| HIGH | `libacl1` | `2.3.2-2+b1` | [CVE-2026-54369](https://security-tracker.debian.org/tracker/CVE-2026-54369) |
| HIGH | `libblkid1` | `2.41.5-0+deb13u1` | [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642), [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408), [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409), [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) |
| HIGH | `liblastlog2-2` | `2.41.5-0+deb13u1` | [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642), [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408), [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409), [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) |
| HIGH | `libmount1` | `2.41.5-0+deb13u1` | [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642), [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408), [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409), [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) |
| HIGH | `libncursesw6` | `6.5+20250216-2` | [CVE-2025-69720](https://security-tracker.debian.org/tracker/CVE-2025-69720) |
| HIGH | `libsmartcols1` | `2.41.5-0+deb13u1` | [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642), [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408), [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409), [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) |
| HIGH | `libsqlite3-0` | `3.46.1-7+deb13u2` | [CVE-2026-119373](https://security-tracker.debian.org/tracker/CVE-2026-119373), [CVE-2026-119374](https://security-tracker.debian.org/tracker/CVE-2026-119374), [CVE-2026-119379](https://security-tracker.debian.org/tracker/CVE-2026-119379) |
| HIGH | `libssl3t64` | `3.5.9-1~deb13u1` | [CVE-2026-107494](https://security-tracker.debian.org/tracker/CVE-2026-107494), [CVE-2026-124709](https://security-tracker.debian.org/tracker/CVE-2026-124709) |
| HIGH | `libsystemd0` | `257.13-1~deb13u1` | [CVE-2026-16742](https://security-tracker.debian.org/tracker/CVE-2026-16742) |
| HIGH | `libtinfo6` | `6.5+20250216-2` | [CVE-2025-69720](https://security-tracker.debian.org/tracker/CVE-2025-69720) |
| HIGH | `libudev1` | `257.13-1~deb13u1` | [CVE-2026-16742](https://security-tracker.debian.org/tracker/CVE-2026-16742) |
| HIGH | `libuuid1` | `2.41.5-0+deb13u1` | [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642), [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408), [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409), [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) |
| HIGH | `login` | `1:4.16.0-2+really2.41.5-0+deb13u1` | [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642), [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408), [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409), [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) |
| HIGH | `mount` | `2.41.5-0+deb13u1` | [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642), [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408), [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409), [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) |
| HIGH | `ncurses-base` | `6.5+20250216-2` | [CVE-2025-69720](https://security-tracker.debian.org/tracker/CVE-2025-69720) |
| HIGH | `ncurses-bin` | `6.5+20250216-2` | [CVE-2025-69720](https://security-tracker.debian.org/tracker/CVE-2025-69720) |
| HIGH | `openssl` | `3.5.9-1~deb13u1` | [CVE-2026-107494](https://security-tracker.debian.org/tracker/CVE-2026-107494), [CVE-2026-124709](https://security-tracker.debian.org/tracker/CVE-2026-124709) |
| HIGH | `openssl-provider-legacy` | `3.5.9-1~deb13u1` | [CVE-2026-107494](https://security-tracker.debian.org/tracker/CVE-2026-107494), [CVE-2026-124709](https://security-tracker.debian.org/tracker/CVE-2026-124709) |
| HIGH | `perl-base` | `5.40.1-6+deb13u1` | [CVE-2026-9538](https://security-tracker.debian.org/tracker/CVE-2026-9538) |
| HIGH | `util-linux` | `2.41.5-0+deb13u1` | [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642), [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408), [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409), [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) |

These findings retain the image security gate. Track Debian fixes, rebuild against the updated base and rerun the scanner. Consider a smaller supported base only after rerunning the migration, PDF, cryptography and database integration suites.

## Remediation candidate rescan

Trivy 0.69.3 used vulnerability database schema 2, updated at `2026-11-24T19:04:37Z` and downloaded at `2026-11-24T20:34:42Z`. Timestamps are UTC. Sizes are Docker's reported image size in bytes before rounding.

| Image | Local image identifier | Size | HIGH | CRITICAL |
|---|---|---:|---:|---:|
| Refreshed current Trixie backend | `sha256:426d2a4f44399e8a6e1a5b0e6a298b6359436fe7f0e1ba9104ad937d82be9daa` | 437,163,131 | 53 | 0 |
| Experimental hardened Debian 13 backend | `sha256:2f96a98a60e238873fd15a27de199fa38aaa448db4b88af02e3d53ae278c507a` | 364,817,182 | 21 | 0 |
| Verified web image | `sha256:448303379a4c381aa6b3e6dc9371d5d2b16f792c6b88374b1210e9c9521aa95f` | 351,927,992 | 0 | 0 |

- `docker pull python:3.12-slim-trixie` resolved the same digest as the official slim image used by the build. `apt-get update` followed by `apt-get -s upgrade` showed zero supported pending updates. `docker build --pull -t insightforge-api-m8-trixie ./backend` reproduced the 53 findings.
- `apt-get -s purge mount login openssl ncurses-bin perl-base` would remove the CA certificate bundle along with essential Debian packages. The simulated purge was rejected for TLS compatibility and maintainability.
- Built the experimental image with `docker build -f backend/.venv-m2/m8-tools/Dockerfile.hardened -t insightforge-api-m8-hardened ./backend`. It used a multi-stage copy of the installed CPython 3.12 wheel dependencies into the public Docker Hardened Images Debian 13 runtime. It is 16.5% smaller than the refreshed application image, runs as UID 65532, and passed Python imports and HTTP health with a read-only root, all capabilities dropped and no network. It still introduces platform changes to executable paths, diagnostics and volume ownership. Full candidate-specific live jobs and recovery were not verified, so the checked-in base remains the official slim image.
- The hardened candidate has HIGH findings in libexpat1 (4), ncurses (4 binary matches), SQLite (3), OpenSSL (6 binary matches), and libuuid1 (4). The four libexpat1 advisories are CVE-2026-113351, CVE-2026-115506, CVE-2026-117610 and CVE-2026-118370.
- All 53 official-image findings were correlated to 13 distinct CVEs, checked against Debian's Trixie status, and documented with application reachability and proposed review ownership. [Risk register](M8_VULNERABILITY_RISK.md)

## Resolved findings

- The initial backend scan found CRITICAL `GHSA-6q48-6c7w-9gw2` in certifi 2026.6.17 and HIGH findings in msgpack 1.1.2, urllib3 2.7.0 and setuptools 70.3.0. Installed certifi 2026.10.18, msgpack 1.2.3 and urllib3 2.8.0, then removed pip and ensurepip from the runtime to remove vulnerable vendored copies. These findings disappeared on rescan.
- The initial frontend image reported ten HIGH matches for brace-expansion, ip-address, pacote, picomatch and sigstore in runtime package-manager tooling. Removing global npm and Corepack from the final image cleared all ten.
- The initial IaC scan identified CRITICAL AWS-0104 for five task egress rules, HIGH AWS-0053 for the ALB, and HIGH AWS-0132 for the report bucket. Restricted VPC endpoints, the internal ALB and a rotating customer-managed key cleared these findings.

## Commands

```text
trivy image --scanners vuln --severity HIGH,CRITICAL --format json insightforgeai-api
trivy image --scanners vuln --severity HIGH,CRITICAL --format json insightforgeai-web
trivy config --severity HIGH,CRITICAL --exit-code 1 infra/terraform/staging
trivy fs --scanners secret --severity HIGH,CRITICAL --exit-code 1 <source-snapshot>
python -m pip_audit --strict -r requirements-dev.txt
npm audit --omit=dev --json
```

The source snapshot excluded ignored local environment files, test data and scanner reports. The source secret and Terraform scans reported zero HIGH/CRITICAL results. The IaC scanner ran without staging variables and skipped one unrelated CloudFront check that required a newer Trivy engine. The dependency audits separately reported zero findings. The repository CI enforces the image and IaC severity gates.
