# credentials.py -- support for git credential helpers

# Copyright (C) 2022 Daniele Trifirò <daniele@iterative.ai>
#
# Dulwich is dual-licensed under the Apache License, Version 2.0 and the GNU
# General Public License as public by the Free Software Foundation; version 2.0
# or (at your option) any later version. You can redistribute it and/or
# modify it under the terms of either of these two licenses.
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# You should have received a copy of the licenses; if not, see
# <http://www.gnu.org/licenses/> for a copy of the GNU General Public License
# and <http://www.apache.org/licenses/LICENSE-2.0> for a copy of the Apache
# License, Version 2.0.
#

"""Support for git credential helpers

https://git-scm.com/book/en/v2/Git-Tools-Credential-Storage

Currently Dulwich supports only the `get` operation

"""
import os
import shlex
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Union

from dulwich.config import ConfigDict


class CredentialNotFoundError(Exception):
    pass


class CredentialHelper:
    """Helper for retrieving credentials for http/https git remotes

    Usage:
    >>> helper = CredentialHelper("store") # Use `git credential-store`
    >>> credentials = helper.get("https://github.com/dtrifiro/aprivaterepo")
    >>> username = credentials["username"]
    >>> password = credentials["password"]
    """

    def __init__(self, command: str):
        self._command = command
        self._run_kwargs: Dict[str, Any] = {}
        if self._command[0] == "!":
            # On Windows this will only work in git-bash and/or WSL2
            self._run_kwargs["shell"] = True

    def _prepare_command(self) -> Union[str, List[str]]:
        if self._command[0] == "!":
            return self._command[1:]

        argv = shlex.split(self._command)
        if sys.platform == "win32":
            # Windows paths are mangled by shlex
            argv[0] = self._command.split(maxsplit=1)[0]

        if os.path.isabs(argv[0]):
            return argv

        executable = f"git-credential-{argv[0]}"
        if not shutil.which(executable) and shutil.which("git"):
            # If the helper cannot be found in PATH, it might be
            # a C git helper in GIT_EXEC_PATH
            git_exec_path = subprocess.check_output(
                ("git", "--exec-path"),
                universal_newlines=True,  # TODO: replace universal_newlines with `text` when dropping 3.6
            ).strip()
            if shutil.which(executable, path=git_exec_path):
                executable = os.path.join(git_exec_path, executable)

        return [executable, *argv[1:]]

    def get(self, url: str) -> Dict[bytes, bytes]:
        cmd = self._prepare_command()
        if isinstance(cmd, str):
            cmd += " get"
        else:
            cmd.append("get")

        helper_input = f"url={url}{os.linesep}".encode("ascii")

        try:
            res = subprocess.run(  # type: ignore # breaks on 3.6
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                input=helper_input,
                **self._run_kwargs,
            )
        except subprocess.CalledProcessError as exc:
            raise CredentialNotFoundError(exc.stderr) from exc
        except FileNotFoundError as exc:
            raise CredentialNotFoundError("Helper not found") from exc

        credentials = {}
        for line in res.stdout.strip().splitlines():
            try:
                key, value = line.split(b"=")
                credentials[key] = value
            except ValueError:
                continue

        if not all(
            (credentials, b"username" in credentials, b"password" in credentials)
        ):
            raise CredentialNotFoundError("Could not get credentials from helper")

        return credentials

    def store(self, *args, **kwargs):
        """Store the credential, if applicable to the helper"""
        raise NotImplementedError

    def erase(self, *args, **kwargs):
        """Remove a matching credential, if any, from the helper’s storage"""
        raise NotImplementedError

    @classmethod
    def from_config(
        cls, config: ConfigDict, url: Optional[str] = None
    ) -> "CredentialHelper":
        # We will try to get the url-specific credential section, in case that
        # is not defined, config.get() will fallback to the generic section.
        encoding = config.encoding or sys.getdefaultencoding()
        section = (b"credential", url.encode(encoding)) if url else (b"credential",)
        command = config.get(section, b"helper")
        assert command and isinstance(command, bytes)
        return cls(command.decode(encoding))
