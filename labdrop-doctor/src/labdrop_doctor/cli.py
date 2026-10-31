from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


REQUIRED_MODULES = ("pyte", "typer", "rich")
MIN_FREE_BYTES = 256 * 1024 * 1024


def check_python(version: tuple[int, int, int] | None = None) -> Check:
    version = version or sys.version_info[:3]
    rendered = ".".join(map(str, version))
    ok = version >= (3, 10)
    return Check("Python", "PASS" if ok else "FAIL", f"Python {rendered} (requires >=3.10)")


def check_platform(system: str | None = None, release: str | None = None) -> Check:
    system = system or platform.system()
    release = release or platform.release()
    status = "PASS" if system == "Linux" else "WARN"
    platform_note = " (target platform)" if status == "PASS" else " (Linux is the target platform)"
    detail = f"{system} {release}{platform_note}"
    return Check("Platform", status, detail)


def check_disk(path: Path, free_bytes: int | None = None) -> Check:
    try:
        available = shutil.disk_usage(path).free if free_bytes is None else free_bytes
    except OSError as exc:
        return Check("Disk space", "FAIL", f"Cannot inspect {path}: {exc}")
    mib = available / (1024 * 1024)
    status = "PASS" if available >= MIN_FREE_BYTES else "WARN"
    return Check("Disk space", status, f"{mib:.0f} MiB free (recommend at least 256 MiB)")


def check_directory(path: Path) -> Check:
    if not path.is_dir():
        return Check("Current directory", "FAIL", f"{path} is not a directory")
    probe: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".labdrop-doctor-", dir=path, delete=False) as file:
            probe = file.name
            file.write(b"labdrop-doctor")
        if Path(probe).read_bytes() != b"labdrop-doctor":
            return Check("Current directory", "FAIL", f"Read-back failed in {path}")
        return Check("Current directory", "PASS", f"Read/write works in {path}")
    except OSError as exc:
        return Check("Current directory", "FAIL", f"Read/write failed in {path}: {exc}")
    finally:
        if probe is not None:
            try:
                os.unlink(probe)
            except OSError:
                pass


def check_dependencies(find_spec: Callable[[str], object] = importlib.util.find_spec) -> Check:
    missing = [name for name in REQUIRED_MODULES if find_spec(name) is None]
    if missing:
        detail = f"Missing: {', '.join(missing)}; install LabDrop requirements"
        return Check("LabDrop dependencies", "FAIL", detail)
    return Check("LabDrop dependencies", "PASS", f"Available: {', '.join(REQUIRED_MODULES)}")


def check_receiver(url: str, timeout: float = 3.0) -> Check:
    if not url.startswith(("http://", "https://")):
        return Check("Receiver", "WARN", "URL must start with http:// or https://")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return Check("Receiver", "PASS", f"Reached {url} (HTTP {response.status})")
    except urllib.error.HTTPError as exc:
        return Check("Receiver", "PASS", f"Reached {url} (HTTP {exc.code})")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return Check("Receiver", "WARN", f"Could not reach {url}: {exc}")


def run_checks(path: Path, receiver_url: str | None = None) -> list[Check]:
    checks = [
        check_python(),
        check_platform(),
        check_disk(path),
        check_directory(path),
        check_dependencies(),
    ]
    if receiver_url:
        checks.append(check_receiver(receiver_url))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check whether this computer is ready to use LabDrop."
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--receiver-url", help="optionally check an instructor receiver URL")
    parser.add_argument(
        "--path",
        type=Path,
        default=Path.cwd(),
        help="directory to check (default: current directory)",
    )
    args = parser.parse_args(argv)
    checks = run_checks(args.path, args.receiver_url)
    if args.json:
        output = {"checks": [asdict(check) for check in checks], "summary": _summary(checks)}
        print(json.dumps(output, indent=2))
    else:
        for check in checks:
            print(f"{check.status:4}  {check.name}: {check.detail}")
        print(f"\n{_summary(checks)}")
    return 1 if any(check.status == "FAIL" for check in checks) else 0


def _summary(checks: list[Check]) -> str:
    counts = {
        status: sum(item.status == status for item in checks)
        for status in ("PASS", "WARN", "FAIL")
    }
    return "Summary: " + ", ".join(f"{status} {count}" for status, count in counts.items())


if __name__ == "__main__":
    raise SystemExit(main())
