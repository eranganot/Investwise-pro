"""The environment must not be allowed to lie about what the project pins.

CI #182 failed all three jobs, and the Railway build for c52cb17d failed with
it, before a single test ran:

    ERROR: Cannot install -r requirements.txt (line 19), -r requirements.txt
    (line 25) and cryptography==44.0.0 because these package versions have
    conflicting dependencies.
        pywebpush 2.3.0 depends on cryptography>=2.6.1
        py-vapid 1.9.4 depends on cryptography>=46

PR 22 pinned ``py-vapid==1.9.4`` to close an open lower bound. Measured from the
published wheel metadata, after the fact:

    py-vapid 1.9.2   Requires-Dist: cryptography >=2.5
    py-vapid 1.9.4   Requires-Dist: cryptography >=46

requirements.txt line 15 pins ``cryptography==44.0.0``. The pin was
unsatisfiable the moment it was written.

WHY IT WAS NOT CAUGHT, which is the part worth fixing. The pin was chosen by
reading the version installed in the development sandbox -- where cryptography
was **50.0.1**, not the 44.0.0 this repo pins. ``pip install py-vapid==1.9.4``
succeeded there and proved nothing about the project. A 1382-test green run
proved nothing either, because a dependency-resolution failure happens BEFORE
pytest is reachable; no test inside the suite can ever observe it.

So the suite cannot check resolution. What it can check -- and what would have
stopped this -- is the assumption underneath the mistake: that the interpreter
running these tests is holding the versions requirements.txt actually asks for.
The moment those diverge, every conclusion drawn from "it works here" is
unfounded, and this file says so by name.

This is the same shape as the FX incident: a number read out of the local
environment, written down as if it were the project's.
"""
from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest

REQS = Path(__file__).resolve().parents[1] / "requirements.txt"

# Distribution name -> import/metadata name where they differ.
_CANON = {"uvicorn[standard]": "uvicorn", "sqlalchemy[asyncio]": "sqlalchemy"}


# NO OPTIONAL-PACKAGE ESCAPE HATCH, deliberately. An earlier cut of this file
# skipped when a lazily-imported dependency (celery, redis, google-genai) was
# absent. Two reasons that was wrong. First, PR 21 made an unregistered skip
# FAIL the run, so the skip would have become a failure with a less useful
# message than the one below. Second, the app importing something lazily is a
# statement about runtime resilience, not about the test environment: CI runs
# `pip install -r requirements.txt -r requirements-dev.txt`, so anything pinned
# here IS installed, and its absence means the environment is not the project's.


def _pins() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in REQS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+(?:\[[^\]]+\])?)==([^\s;#]+)", line)
        if m:
            out.append((_CANON.get(m.group(1), m.group(1)), m.group(2)))
    return out


def test_every_dependency_is_pinned_with_a_double_equals():
    """An open bound is how two machines legitimately get two libraries.

    That is not hypothetical here -- it is the sentence in PR 22's own commit
    message, about this exact file.
    """
    loose = []
    for raw in REQS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if "==" not in line:
            loose.append(line)
    assert not loose, (
        f"requirements.txt has unpinned entries: {loose}. Every other line is "
        "'==', and the one open bound in this file's history produced a bug "
        "that could not be reproduced on the machine that shipped it.")


def test_the_pin_list_is_not_empty():
    pins = _pins()
    assert len(pins) >= 15, f"only parsed {len(pins)} pins - the parser is wrong"


@pytest.mark.parametrize("name,pinned", _pins(), ids=lambda v: str(v))
def test_the_installed_version_is_the_pinned_version(name, pinned):
    """THE ONE THAT WOULD HAVE CAUGHT PR 22.

    In the sandbox that chose ``py-vapid==1.9.4``, this test would have failed
    on cryptography: pinned 44.0.0, installed 50.0.1. Every "verified locally"
    claim made in that environment about a cryptography-adjacent library was
    unfounded, and nothing said so.
    """
    try:
        installed = version(name)
    except PackageNotFoundError:
        raise AssertionError(
            f"requirements.txt pins {name}=={pinned} and it is NOT INSTALLED. "
            "Tests are running against an environment that does not match the "
            "project.") from None

    assert installed == pinned, (
        f"{name}: requirements.txt pins {pinned}, this interpreter has "
        f"{installed}.\n"
        "Nothing measured in this environment can be quoted as a fact about "
        "the project until they agree. Reinstall with\n"
        "    pip install -r requirements.txt -r requirements-dev.txt")


def test_py_vapid_is_below_the_version_that_demands_cryptography_46():
    """The specific regression, named, so re-introducing it fails HERE.

    Bumping to 1.9.4 is legitimate -- but only together with cryptography>=46,
    and that is a deliberate upgrade of the library every key, token and
    password hash in this app goes through. It is not a side effect of silencing
    a warning.
    """
    pins = dict(_pins())
    assert pins.get("py-vapid") == "1.9.2", (
        f"py-vapid is pinned to {pins.get('py-vapid')}. 1.9.4 requires "
        "cryptography>=46; this file pins cryptography==44.0.0, so pip cannot "
        "resolve it and CI dies before pytest starts. If you are deliberately "
        "moving to 1.9.4, raise the cryptography pin in the SAME commit and "
        "update this test.")

    crypto = pins.get("cryptography", "0")
    major = int(crypto.split(".")[0])
    assert major < 46, (
        f"cryptography is now {crypto}. py-vapid 1.9.4 is installable again - "
        "reconsider the pin above, and fix this test either way.")


def test_the_vapid_keypair_does_not_need_py_vapid_at_all():
    """Why 1.9.2 is SAFE rather than merely installable.

    The deprecated `ec.generate_private_key(ec.SECP256R1, ...)` in 1.9.2 lives
    at py_vapid/__init__.py:200, inside `generate_keys()`. PR 22 stopped calling
    it: the keypair is built with `cryptography` directly. The py_vapid path
    still in use, `Vapid01.from_raw`, already passes an INSTANTIATED curve.

    So this asserts the thing that makes the older pin acceptable -- if someone
    reinstates the library call, the pin becomes unsafe and this fails.
    """
    import ast
    import inspect
    import textwrap

    from app.services.push_service import _generate_vapid

    full = textwrap.dedent(inspect.getsource(_generate_vapid))
    # THE DOCSTRING NAMES generate_keys, to explain why it is not called. Search
    # the executable body only -- a prose mention is the opposite of a call, and
    # the first cut of this test failed on its own explanation.
    fn = ast.parse(full).body[0]
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                           and isinstance(getattr(fn.body[0], "value", None), ast.Constant)
                           and isinstance(fn.body[0].value.value, str)) else fn.body
    src = "\n".join(ast.unparse(node) for node in body)

    assert "generate_keys" not in src, (
        "push_service is calling py_vapid's generate_keys() again. That is the "
        "function carrying the deprecated curve-as-class call on py-vapid "
        "1.9.2, which requirements.txt now pins.")
    assert "ec.generate_private_key(ec.SECP256R1())" in src, (
        "the keypair is no longer built directly from cryptography with an "
        "instantiated curve")
