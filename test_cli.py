import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import signal
import unittest
from unittest.mock import patch

import consumer
from operation_lock import RepositoryLock


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.config = self.root / "config.json"
        config = consumer.default_config(str(self.root))
        config["destination"] = {"type": "other", "repo": str(self.repo), "label": "Test"}
        consumer.write_config(self.config, config)
        self.env = dict(os.environ, HOME=str(self.root), XDG_STATE_HOME=str(self.root / "state"),
                        KEEP_LOG_DIR=str(self.root / "logs"), BORG_PASSPHRASE="",
                        BORG_CONFIG_DIR=str(self.root / "borg-config"), BORG_CACHE_DIR=str(self.root / "cache"),
                        BORG_SECURITY_DIR=str(self.root / "security"))

    def cli(self, *arguments):
        return subprocess.run([sys.executable, "-S", "cli.py", "--config", str(self.config), *arguments],
                              env=self.env, capture_output=True, text=True, timeout=30)

    def test_no_site_packages_required(self):
        for command in ("status", "logs", "doctor"):
            result = self.cli(command, "--json")
            self.assertIn(result.returncode, (0, 1), result.stderr)
            self.assertEqual(json.loads(result.stdout)["schema_version"], 1)

    def test_cross_process_busy_and_release(self):
        (self.repo / "config").write_text("[repository]\n", encoding="utf-8")
        with patch.dict(os.environ, self.env):
            with RepositoryLock(str(self.repo)):
                result = self.cli("archives", "--json")
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            with RepositoryLock(str(self.repo)):
                pass

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux process groups")
    def test_check_cancellation_persists_non_success(self):
        (self.repo / "config").write_text("[repository]\n", encoding="utf-8")
        bindir = self.root / "bin"
        bindir.mkdir()
        marker = self.root / "child-started"
        fake = bindir / "borg"
        fake.write_text("#!/usr/bin/python3\nfrom pathlib import Path\nimport time\nPath(" + repr(str(marker)) + ").touch()\ntime.sleep(60)\n")
        fake.chmod(0o755)
        env = dict(self.env, PATH=str(bindir) + os.pathsep + self.env.get("PATH", ""))
        process = subprocess.Popen([sys.executable, "-S", "cli.py", "--config", str(self.config), "check", "--json"],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(marker.exists())
            process.send_signal(signal.SIGTERM)
            out, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 130, out + err)
            self.assertEqual(json.loads(out)["exit_code"], 130)
            record = json.loads((self.root / "state/keep/last-check.json").read_text())
            self.assertEqual(record["result"], "cancelled")
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=10)

    @unittest.skipUnless(sys.platform == "linux" and shutil.which("borg"), "Requires Borg on Linux")
    def test_real_archive_query_and_check(self):
        subprocess.run(["borg", "init", "--encryption=none", str(self.repo)], env=self.env, check=True, capture_output=True)
        for arguments in (("archives",), ("check",), ("check", "--deep")):
            result = self.cli(*arguments, "--json")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["exit_code"], 0)
        status = json.loads(self.cli("status", "--json").stdout)
        self.assertEqual(status["data"]["last_integrity_check"]["result"], "success")


if __name__ == "__main__":
    unittest.main()
