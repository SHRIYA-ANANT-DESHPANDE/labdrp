import json

from labdrop_doctor import cli


def test_python_version_boundary():
    assert cli.check_python((3, 9, 9)).status == "FAIL"
    assert cli.check_python((3, 10, 0)).status == "PASS"


def test_platform_target_and_other_os():
    assert cli.check_platform("Linux", "6.1").status == "PASS"
    assert cli.check_platform("Windows", "11").status == "WARN"


def test_disk_threshold_and_inspection_error(tmp_path, monkeypatch):
    assert cli.check_disk(tmp_path, cli.MIN_FREE_BYTES).status == "PASS"
    assert cli.check_disk(tmp_path, cli.MIN_FREE_BYTES - 1).status == "WARN"

    def fail(_path):
        raise OSError("unavailable")

    monkeypatch.setattr(cli.shutil, "disk_usage", fail)
    assert cli.check_disk(tmp_path).status == "FAIL"


def test_directory_probe_pass_and_failure(tmp_path, monkeypatch):
    assert cli.check_directory(tmp_path).status == "PASS"
    def permission_denied(**_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(cli.tempfile, "NamedTemporaryFile", permission_denied)
    assert cli.check_directory(tmp_path).status == "FAIL"


def test_dependency_availability_is_injected():
    def missing_pyte(name):
        return None if name == "pyte" else object()

    assert cli.check_dependencies(lambda _name: object()).status == "PASS"
    assert cli.check_dependencies(missing_pyte).status == "FAIL"


def test_receiver_success_and_failure(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(cli.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    assert cli.check_receiver("http://receiver.test").status == "PASS"

    def fail(*_args, **_kwargs):
        raise cli.urllib.error.URLError("offline")

    monkeypatch.setattr(cli.urllib.request, "urlopen", fail)
    assert cli.check_receiver("http://receiver.test").status == "WARN"

    def not_found(*_args, **_kwargs):
        raise cli.urllib.error.HTTPError("http://receiver.test", 404, "not found", {}, None)

    monkeypatch.setattr(cli.urllib.request, "urlopen", not_found)
    assert cli.check_receiver("http://receiver.test").status == "PASS"


def test_json_output_and_failure_exit(monkeypatch, capsys, tmp_path):
    checks = [cli.Check("Python", "FAIL", "too old")]
    monkeypatch.setattr(cli, "run_checks", lambda *_args: checks)
    assert cli.main(["--json", "--path", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["checks"][0]["status"] == "FAIL"
    assert result["summary"] == "Summary: PASS 0, WARN 0, FAIL 1"
