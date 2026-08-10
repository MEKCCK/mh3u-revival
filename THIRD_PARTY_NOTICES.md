<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Third-Party Notices

MH3U Revival's original source code is licensed under the GNU Affero General
Public License, version 3 (`AGPL-3.0-only`). The unmodified license
text is in [`LICENSE`](LICENSE).

The project uses, downloads, or documents the following independent works.
Each work remains under its own license; the project license does not replace
those terms.

| Component | Use in this project | License |
| --- | --- | --- |
| [NintendoClients](https://github.com/kinnay/NintendoClients) and the [MH3U fork](https://github.com/Matt-Wood-23/NintendoClients/tree/mh3u-revival) | External NEX/PRUDP/RMC runtime dependency | MIT; copyright Yannik Marchand and contributors |
| [anynet](https://github.com/kinnay/anynet) | Python networking dependency | MIT |
| [EasyTier v2.6.4](https://github.com/EasyTier/EasyTier/tree/v2.6.4) | Separately downloaded P2P mesh executable | LGPL-3.0-only; see the [v2.6.4 license](https://github.com/EasyTier/EasyTier/blob/v2.6.4/LICENSE) |
| [Terracotta](https://github.com/MEKCCK/Terracotta) | Architectural reference for embedded EasyTier orchestration; not a bundled runtime dependency | AGPL-3.0 |
| [AnyIO](https://github.com/agronholm/anyio) | Python runtime dependency | MIT |
| [aioconsole](https://github.com/vxgmichel/aioconsole) | Python runtime dependency | GPL-3.0-or-later |
| [PyCryptodome](https://www.pycryptodome.org/) | Python cryptography dependency | BSD-2-Clause and public-domain components |
| [PyInstaller](https://pyinstaller.org/) | Build tool and frozen bootloader | GPL-2.0-or-later with the PyInstaller bootloader exception |
| [CPython](https://www.python.org/) | Interpreter embedded by frozen builds | Python Software Foundation License Version 2 |
| [Cemu](https://github.com/cemu-project/Cemu) | Separate emulator/client distribution | MPL-2.0 |

EasyTier release archives may include further components, including a Windows
TUN driver. Distributors of a bundle containing those binaries must preserve
the license and notice files shipped in the corresponding EasyTier release
archive. The launcher downloads EasyTier as a separate executable and does not
incorporate its source into MH3U Revival.

Names and trademarks of Nintendo, Capcom, Monster Hunter, Cemu, and the listed
projects belong to their respective owners. Their mention identifies protocol
compatibility or dependencies and does not imply endorsement.

The official hosted-service rules are separate from software copyright terms.
See [`docs/HOSTED_SERVICE_ACCESS_POLICY.md`](docs/HOSTED_SERVICE_ACCESS_POLICY.md).
