import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def generate_totp_secret() -> str:
    """Generates a cryptographically secure 20-byte Base32-encoded TOTP secret."""
    random_bytes = secrets.token_bytes(20)
    return base64.b32encode(random_bytes).decode("utf-8")


def generate_provisioning_uri(secret: str, user_email: str, issuer_name: str = "Amrutam") -> str:
    """Generates standard otpauth:// URI for authenticator apps (Google Authenticator, Authy)."""
    label = f"{issuer_name}:{user_email}"
    encoded_label = quote(label)
    encoded_issuer = quote(issuer_name)
    return f"otpauth://totp/{encoded_label}?secret={secret}&issuer={encoded_issuer}&algorithm=SHA1&digits=6&period=30"


def generate_totp_token(secret: str, interval: int = 30, for_time: float | None = None) -> str:
    """Generates a 6-digit RFC 6238 TOTP token for a given timestamp."""
    if for_time is None:
        for_time = time.time()

    counter = int(for_time // interval)
    counter_bytes = struct.pack(">Q", counter)

    # Pad secret if needed
    cleaned_secret = secret.strip().replace(" ", "").upper()
    missing_padding = len(cleaned_secret) % 8
    if missing_padding:
        cleaned_secret += "=" * (8 - missing_padding)

    key = base64.b32decode(cleaned_secret, casefold=True)
    hmac_digest = hmac.new(key, counter_bytes, hashlib.sha1).digest()

    offset = hmac_digest[-1] & 0x0F
    code_int = struct.unpack(">I", hmac_digest[offset:offset + 4])[0] & 0x7FFFFFFF
    code = code_int % 1000000
    return f"{code:06d}"


def verify_totp_token(secret: str, token: str, window: int = 1, interval: int = 30) -> bool:
    """
    Validates a 6-digit TOTP token allowing for clock skew (default ±1 window = ±30 seconds).
    """
    if not token or len(token.strip()) != 6 or not token.strip().isdigit():
        return False

    token_str = token.strip()
    current_time = time.time()

    for step in range(-window, window + 1):
        test_time = current_time + (step * interval)
        if generate_totp_token(secret, interval=interval, for_time=test_time) == token_str:
            return True

    return False


def generate_scratch_codes(count: int = 5) -> list[str]:
    """Generates emergency backup recovery scratch codes."""
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(4).upper()
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes
