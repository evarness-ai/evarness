"""Which stdio launchers leave the server's own code outside the signature.

The signed spec covers a script file the interpreter is asked to run and, for a local
``-m`` package, a hash of the package's files. A launcher that fetches its code when it
starts (``npx pkg``, ``uvx pkg``, ``pipx run pkg``, ``docker run image``) names no file, so
the signature of ``command`` and ``args`` is all there is to bind: it holds the code still
only if ``args`` pin it to one release (an exact version, an image digest). This module
says when they do not. It is the one rule the signer, the verifier and the launch gate share.
"""

from __future__ import annotations

import re

_VERSION = r"\d+(?:\.\d+){1,3}(?:[-+.][0-9A-Za-z.-]+)?"
#: ``pkg@1.2.3`` / ``@scope/pkg@1.2.3`` (npm, bun, uv) -- an exact release, not ``@latest`` or a range.
_AT_PINNED = re.compile(rf"^(?:@[^/@\s]+/)?[^/@\s]+@{_VERSION}$")
#: ``pkg==1.2.3`` (PEP 440 exact pin; ``===`` too), with optional extras.
_EQ_PINNED = re.compile(rf"^[A-Za-z0-9._-]+(?:\[[^\]]*\])?===?{_VERSION}$")
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")

_NODE_RUNNERS = {"npx", "bunx"}
_PYTHON_RUNNERS = {"uvx", "pipx"}
_CONTAINER_RUNNERS = {"docker", "podman"}


def _name(command: str) -> str:
    stem = re.split(r"[\\/]", command)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _is_python(name: str) -> bool:
    return name == "python" or re.fullmatch(r"python3(?:\.\d+)?", name) is not None


def _first_positional(args: tuple[str, ...], *, skip: int = 0) -> str | None:
    seen = 0
    for arg in args:
        if arg.startswith("-"):
            continue
        if seen == skip:
            return arg
        seen += 1
    return None


def python_module(command: str | None, args: tuple[str, ...]) -> str | None:
    """The module a ``python -m <module>`` launcher runs, else ``None``."""
    if command is None or not _is_python(_name(command)):
        return None
    for index, arg in enumerate(args[:-1]):
        if arg == "-m":
            return args[index + 1]
    return None


def unpinned_launcher(command: str | None, args: tuple[str, ...]) -> str | None:
    """Why the code this launcher runs is not bound by the signature, or ``None``.

    Call it only for a stdio launcher whose script or package was not hashed. ``None`` also
    means "not a launcher this module knows": a plain binary is covered by its own hash.
    """
    if not command:
        return None
    name = _name(command)

    module = python_module(command, args)
    if module is not None:
        return (
            f"`-m {module}` runs a module that is not a package under the server's directory, "
            "so its code is not hashed; run it from a script file or a local package"
        )

    if name in _NODE_RUNNERS:
        target = _first_positional(args)
        if target is None or not _AT_PINNED.match(target):
            return (
                f"`{name} {target or ''}`".strip()
                + " is not pinned to an exact version; use `pkg@1.2.3`"
            )
        return None

    if name in _PYTHON_RUNNERS:
        positional = list(args)
        if name == "pipx" and "run" in positional:
            positional = positional[positional.index("run") + 1 :]
        candidates: list[str] = []
        if "--from" in positional:
            index = positional.index("--from")
            if index + 1 < len(positional):
                candidates.append(positional[index + 1])
        else:
            first = _first_positional(tuple(positional))
            if first:
                candidates.append(first)
        if not candidates or not any(
            _EQ_PINNED.match(c) or _AT_PINNED.match(c) for c in candidates
        ):
            shown = candidates[0] if candidates else ""
            return (
                f"`{name} {shown}`".strip() + " is not pinned to an exact version; use `pkg==1.2.3`"
            )
        return None

    if name in _CONTAINER_RUNNERS:
        if not any(_DIGEST.search(arg) for arg in args):
            return f"`{name}` image is not pinned to a digest; use `image@sha256:<64 hex>`"
        return None

    return None
