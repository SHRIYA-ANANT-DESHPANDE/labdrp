# labdrop-doctor

`labdrop-doctor` is a small Python command-line tool that checks whether a Linux college lab computer is ready to use LabDrop. It checks Python 3.10+, the operating system, free disk space, read/write access to the selected directory, and LabDrop's CLI dependencies (`pyte`, `typer`, and `rich`). It can optionally try a configured receiver URL. The receiver check only confirms an HTTP response; it does not authenticate or submit anything.

## Install

Python 3.10 or newer is required. From this directory, install the tool in a virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead. Windows is reported as a warning because Linux is the target platform.

## Use

```sh
labdrop-doctor
labdrop-doctor --path ./my-assignment
labdrop-doctor --receiver-url http://127.0.0.1:8000
labdrop-doctor --json --receiver-url https://lab.example.edu
```

The current directory is checked by default. Disk space below 256 MiB is a warning. The receiver URL is only contacted when supplied, with a three-second timeout. Exit status is 1 if any check fails and 0 otherwise (warnings alone do not fail the run).

For development and tests:

```sh
python -m pip install -e '.[dev]'
ruff check .
python -m pytest -v
```
