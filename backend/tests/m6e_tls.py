"""Renew certificates in a disposable PostgreSQL source and verify TLS outcomes."""
import argparse
import subprocess
from pathlib import Path
from time import sleep

import psycopg

from m6e_source import OWNER


def run(*args: str) -> None:
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def generate(openssl: str, directory: Path, name: str, days: str) -> Path:
    request = directory / f"{name}.csr"
    cert = directory / f"{name}.crt"
    key = directory / f"{name}.key"
    extensions = directory / "extensions.cnf"
    extensions.write_text("subjectAltName=IP:127.0.0.1\n", encoding="ascii")
    run(openssl, "req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={name}",
        "-keyout", str(key), "-out", str(request))
    run(openssl, "x509", "-req", "-in", str(request), "-signkey", str(key), "-days", days,
        "-extfile", str(extensions), "-out", str(cert))
    return cert


def activate(container: str, cert: Path) -> None:
    for suffix in ("crt", "key"):
        run("docker", "cp", str(cert.with_suffix(f".{suffix}")), f"{container}:/var/lib/postgresql/data/m8-fixture.{suffix}")
    run("docker", "exec", "-u", "root", container, "sh", "-ec",
        'chown postgres:postgres "$PGDATA"/m8-fixture.*; chmod 600 "$PGDATA/m8-fixture.key"')
    run("docker", "exec", container, "psql", "-U", "source_owner", "-d", "external_fixture", "-v", "ON_ERROR_STOP=1",
        "-c", "ALTER SYSTEM SET ssl = on", "-c", "ALTER SYSTEM SET ssl_cert_file = 'm8-fixture.crt'",
        "-c", "ALTER SYSTEM SET ssl_key_file = 'm8-fixture.key'", "-c", "SELECT pg_reload_conf()")


def verify(port: int, ca: Path, rejection: str | None = None) -> None:
    for _ in range(15):
        try:
            with psycopg.connect(host="127.0.0.1", port=port, dbname="external_fixture",
                                 user="source_owner", password=OWNER, connect_timeout=2,
                                 sslmode="verify-full", sslrootcert=str(ca)):
                if rejection is None:
                    return
        except psycopg.OperationalError as exc:
            if rejection and rejection in str(exc).lower():
                return
        sleep(0.25)
    raise RuntimeError(f"TLS fixture did not produce the expected outcome ({rejection or 'trusted'})")


def verify_expiry(openssl: str, port: int, ca: Path) -> None:
    for _ in range(15):
        result = subprocess.run(
            [openssl, "s_client", "-starttls", "postgres", "-connect", f"127.0.0.1:{port}",
             "-CAfile", str(ca), "-verify_return_error"],
            input=b"", capture_output=True, check=False, timeout=5,
        )
        if result.returncode and b"certificate has expired" in result.stderr.lower():
            return
        sleep(0.25)
    raise RuntimeError("PostgreSQL did not present the expired fixture certificate")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("container")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--openssl", default="openssl")
    parser.add_argument("--port", type=int, default=15439)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    expired = generate(args.openssl, args.directory, "expired", "0")
    valid = generate(args.openssl, args.directory, "server", "2")
    untrusted = generate(args.openssl, args.directory, "untrusted", "2")
    expired_check = subprocess.run(
        [args.openssl, "x509", "-in", str(expired), "-checkend", "0", "-noout"],
        capture_output=True, check=False,
    )
    if expired_check.returncode != 1:
        raise RuntimeError("Expected an expired fixture certificate")
    activate(args.container, expired)
    verify_expiry(args.openssl, args.port, expired)
    verify(args.port, expired, "certificate verify failed")
    activate(args.container, valid)
    verify(args.port, valid)
    verify(args.port, untrusted, "certificate verify failed")
    print("Disposable connector TLS fixture passed expired, trusted and wrong-CA checks")


if __name__ == "__main__":
    main()
