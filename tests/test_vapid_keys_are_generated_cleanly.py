"""Web push must not be one dependency upgrade away from breaking.

Eran's gate, 2026-08-30: `1371 passed, 2 warnings`, both

    py_vapid/__init__.py:200: CryptographyDeprecationWarning: Curve argument
    must be an instance of an EllipticCurve class. Did you pass a class by
    mistake? This will be an exception in a future version of cryptography
        self.private_key = ec.generate_private_key(ec.SECP256R1,

Not cosmetic. `cryptography` says in the message itself that this becomes an
**exception**, and the call is `Vapid.generate_keys()` -- the one thing
`push_service._generate_vapid` used py_vapid for. On the upgrade that turns the
warning into an error, VAPID key generation raises and web push stops, for a
reason nobody would connect to notifications.

It also could not be found by reading our own code, and did not reproduce in the
sandbox: **which** py_vapid is installed decides whether it fires. 1.9.4 has it
fixed (`ec.SECP256R1()`); older releases pass the class. `pywebpush==2.3.0`
requires only ``py-vapid>=1.7.0`` -- an open lower bound, in a requirements file
where every other line is ``==``. Two machines legitimately resolved two
different libraries and only one of them warned.

So both halves are fixed: `requirements.txt` pins `py-vapid==1.9.4`, and
`_generate_vapid` builds the keypair with `cryptography` directly, which is what
the surrounding lines were already doing. A VAPID key is an ordinary EC P-256
keypair; py_vapid was being asked for one call it does not need to own.
"""
import base64
import warnings


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_generating_a_keypair_raises_no_deprecation_warning():
    """The warning IS the bug report -- it names its own future exception."""
    from app.services.push_service import _generate_vapid

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _generate_vapid()

    offenders = [str(w.message) for w in caught
                 if "EllipticCurve" in str(w.message)
                 or "future version of cryptography" in str(w.message)]
    assert not offenders, (
        "VAPID key generation still goes through the deprecated curve-as-class "
        f"call, which cryptography will turn into an exception: {offenders}")


def test_the_keypair_is_a_real_p256_pair_in_the_shape_web_push_expects():
    """Changing HOW the key is made must not change WHAT is stored.

    The stored values are raw, base64url, unpadded: a 32-byte private scalar and
    a 65-byte uncompressed point starting 0x04. Anything else and every existing
    subscription breaks on the next push.
    """
    from app.services.push_service import _generate_vapid

    pub_b64, priv_b64 = _generate_vapid()
    assert "=" not in pub_b64 and "=" not in priv_b64, "padding would break the header"

    priv_raw, pub_raw = _b64url_decode(priv_b64), _b64url_decode(pub_b64)
    assert len(priv_raw) == 32, f"private scalar is {len(priv_raw)} bytes, not 32"
    assert len(pub_raw) == 65, f"public point is {len(pub_raw)} bytes, not 65"
    assert pub_raw[0] == 0x04, "public key is not an uncompressed EC point"


def test_py_vapid_can_still_load_the_key_we_generate():
    """The round trip is the point: pywebpush signs with exactly this value.

    Generating the key ourselves is only safe if the library that USES it still
    accepts it, and the public key it derives is the one we handed out to the
    browser. A mismatch here is the 403 that silently stops every notification.
    """
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01

    from app.services.push_service import _generate_vapid

    pub_b64, priv_b64 = _generate_vapid()
    loaded = Vapid01.from_raw(priv_b64.encode())
    derived = loaded.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    assert base64.urlsafe_b64encode(derived).rstrip(b"=").decode() == pub_b64, (
        "py_vapid derives a different public key from our private key -- every "
        "existing subscription would start failing its VAPID signature")


def test_two_calls_do_not_return_the_same_key():
    """A fixed keypair would be a far worse bug than a warning."""
    from app.services.push_service import _generate_vapid

    assert _generate_vapid()[0] != _generate_vapid()[0]
