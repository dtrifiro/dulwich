import os
import shutil
import subprocess
import sys
import tempfile
from unittest import mock, skipIf, skipUnless

from dulwich.config import ConfigDict
from dulwich.credentials import CredentialHelper, CredentialNotFoundError
from dulwich.tests import TestCase


class CredentialHelperTests(TestCase):
    def test_prepare_command_shell(self):
        command = """!f() { echo foo}; f"""

        helper = CredentialHelper(command)
        self.assertEqual(helper._prepare_command(), command[1:])

    def test_prepare_command_abspath(self):
        executable_path = os.path.join(os.sep, "path", "to", "executable")

        helper = CredentialHelper(executable_path)
        self.assertEqual(helper._prepare_command(), [executable_path])

    @skipIf(sys.platform == "win32", reason="Path handling on windows is different")
    def test_prepare_command_abspath_extra_args(self):
        executable_path = os.path.join(os.sep, "path", "to", "executable")
        helper = CredentialHelper(
            f'{executable_path} --foo bar --quz "arg with spaces"'
        )
        self.assertEqual(
            helper._prepare_command(),
            [executable_path, "--foo", "bar", "--quz", "arg with spaces"],
        )

    @mock.patch("shutil.which")
    def test_prepare_command_in_path(self, which):
        which.return_value = True

        helper = CredentialHelper("foo")
        self.assertEqual(helper._prepare_command(), ["git-credential-foo"])

    @mock.patch("subprocess.check_output")
    def test_prepare_command_cli_git_helpers(self, check_output):
        git_exec_path = os.path.join(os.sep, "path", "to", "git-core")
        check_output.return_value = git_exec_path

        def which_mock(arg, **kwargs):
            if arg == "git" or "path" in kwargs:
                return True
            return False

        helper = CredentialHelper("foo")
        expected = [os.path.join(git_exec_path, "git-credential-foo")]

        with mock.patch.object(shutil, "which", new=which_mock):
            self.assertEqual(helper._prepare_command(), expected)

    @skipIf(sys.platform == "win32", reason="Path handling on windows is different")
    @mock.patch("shutil.which")
    def test_prepare_command_extra_args(self, which):
        which.return_value = True

        helper = CredentialHelper('foo --bar baz --quz "arg with spaces"')
        command = helper._prepare_command()
        self.assertEqual(
            command,
            [
                "git-credential-foo",
                "--bar",
                "baz",
                "--quz",
                "arg with spaces",
            ],
        )

    def test_get_nonexisting_executable(self):
        helper = CredentialHelper("nonexisting")
        with self.assertRaises(CredentialNotFoundError):
            helper.get("dummy")

    def test_get_nonexisting_executable_abspath(self):
        path = os.path.join(os.sep, "path", "to", "nonexisting")
        helper = CredentialHelper(path)
        with self.assertRaises(CredentialNotFoundError):
            helper.get("dummy")

    @mock.patch("shutil.which")
    @mock.patch("subprocess.run")
    def test_get(self, run, which):
        run.return_value.stdout = os.linesep.join(
            ["username=username", "password=password", ""]
        ).encode("UTF-8")
        which.return_value = True

        helper = CredentialHelper("foo")
        credentials = helper.get("https://example.com")
        self.assertEqual(credentials[b"username"], b"username")
        self.assertEqual(credentials[b"password"], b"password")

    @skipIf(
        os.name == "nt", reason="On Windows, this only will work for git-bash or WSL2"
    )
    def test_get_shell(self):
        command = """!f() { printf "username=username\npassword=password"; }; f"""
        helper = CredentialHelper(command)
        credentials = helper.get("dummy")
        self.assertEqual(credentials[b"username"], b"username")
        self.assertEqual(credentials[b"password"], b"password")

    @mock.patch("subprocess.run")
    def test_get_failing_command(self, run):
        run.return_value.stderr = b"error message"
        run.return_value.returncode = 1
        with self.assertRaises(CredentialNotFoundError, msg=b"error message"):
            CredentialHelper("dummy").get("dummy")

    @mock.patch("shutil.which")
    @mock.patch("subprocess.run")
    def test_get_missing_username(self, run, which):
        run.return_value.stdout = b"password=password"
        which.return_value = True
        with self.assertRaises(CredentialNotFoundError):
            CredentialHelper("dummy").get("dummy")

    @mock.patch("shutil.which")
    @mock.patch("subprocess.run")
    def test_get_missing_password(self, run, which):
        run.return_value.stdout = b"username=username"
        which.return_value = True
        with self.assertRaises(CredentialNotFoundError):
            CredentialHelper("dummy").get("dummy")

    @mock.patch("shutil.which")
    @mock.patch("subprocess.run")
    def test_get_malformed_output(self, run, which):
        run.return_value.stdout = os.linesep.join(["username", "password", ""]).encode(
            "UTF-8"
        )
        which.return_value = True

        with self.assertRaises(CredentialNotFoundError):
            CredentialHelper("dummy").get("dummy")

    def test_store(self):
        with self.assertRaises(NotImplementedError):
            CredentialHelper("dummy").store()

    def test_erase(self):
        with self.assertRaises(NotImplementedError):
            CredentialHelper("dummy").erase()

    def test_from_config(self):
        config = ConfigDict()
        config.set(b"credential", b"helper", b"generichelper")
        helper = CredentialHelper.from_config(config)
        self.assertEqual(helper._command, "generichelper")

    def test_from_config_with_url(self):
        config = ConfigDict()
        config.set((b"credential", b"https://git.sr.ht"), b"helper", b"urlspecific")
        helper = CredentialHelper.from_config(config, url="https://git.sr.ht")
        self.assertEqual(helper._command, "urlspecific")

    def test_from_config_no_helper(self):
        config = ConfigDict()
        with self.assertRaises(KeyError):
            CredentialHelper.from_config(config)

    def test_from_config_multiple_sections(self):
        config = ConfigDict()
        config.set(b"credential", b"helper", b"generichelper")
        config.set((b"credential", b"https://git.sr.ht"), b"helper", b"urlspecific")

        helper = CredentialHelper.from_config(config)
        self.assertEqual(helper._command, "generichelper")
        helper = CredentialHelper.from_config(config, url="https://git.sr.ht")
        self.assertEqual(helper._command, "urlspecific")


@skipUnless(shutil.which("git"), "requires git cli")
class CredentialHelperCredentialStore(TestCase):
    """tests CredentialHandler with `git credential-store`"""

    def setUp(self):
        super().setUp()
        self.encoding = sys.getdefaultencoding()
        self.store_path = os.path.join(
            tempfile.gettempdir(), "dulwich-git-credential-store-test"
        )
        self.git_exec_path = subprocess.check_output(
            ["git", "--exec-path"], universal_newlines=True
        ).strip()

        self.urls = (
            ("https://example.com", "username", "password"),
            ("https://example1.com", "username1", "password1"),
        )

        for url, username, password in self.urls:
            subprocess_in = os.linesep.join(
                [f"url={url}", f"username={username}", f"password={password}", ""]
            ).encode(self.encoding)
            subprocess.run(
                f"git credential-store --file {self.store_path} store".split(" "),
                input=subprocess_in,
            )

        self.helper = CredentialHelper(f"store --file {self.store_path}")

    def tearDown(self):
        super().tearDown()
        os.unlink(self.store_path)

    def test_init(self):
        expected = [
            os.path.join(self.git_exec_path, "git-credential-store"),
            "--file",
            self.store_path,
        ]
        self.assertEqual(self.helper._prepare_command(), expected)

    def test_get(self):
        for url, username, password in self.urls:
            credentials = self.helper.get(url)
            self.assertEqual(credentials[b"username"], username.encode(self.encoding))
            self.assertEqual(credentials[b"password"], password.encode(self.encoding))

    def test_missing(self):
        with self.assertRaises(CredentialNotFoundError):
            self.helper.get("https://dummy.com")

    def test_store(self):
        with self.assertRaises(NotImplementedError):
            self.helper.store()

    def test_erase(self):
        with self.assertRaises(NotImplementedError):
            self.helper.erase()
