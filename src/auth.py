import base64
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def load_private_key(pem_path: Path):
    with open(pem_path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def sign_pss(private_key, timestamp_ms: str, method: str, path: str) -> str:
    """
    Sign a Kalshi API request using RSA-PSS / SHA-256.

    Message format: f"{timestamp_ms}{METHOD}{/trade-api/v2/path}"
    The path MUST include the /trade-api/v2 prefix.
    """
    message = f"{timestamp_ms}{method.upper()}{path}".encode()
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode()


def make_auth_headers(private_key, method: str, path: str) -> dict[str, str]:
    """
    Returns the three Kalshi auth headers for a request.
    `path` should be the full path including /trade-api/v2 prefix.
    """
    ts = str(int(time.time() * 1000))
    sig = sign_pss(private_key, ts, method, path)
    return {
        "KALSHI-ACCESS-KEY": "",  # filled by KalshiClient which holds api_key
        "KALSHI-ACCESS-SIGNATURE": sig,
        "KALSHI-ACCESS-TIMESTAMP": ts,
    }
