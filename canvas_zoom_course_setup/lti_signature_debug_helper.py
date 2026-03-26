from __future__ import annotations

import argparse
from base64 import b64encode, urlsafe_b64encode

from .utils import build_lti_signature_base_string, build_lti_signature_digest, build_lti_signature_parts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print Zoom LTI signature debug variants for troubleshooting.")
    parser.add_argument("--key", required=True, help="Zoom LTI key")
    parser.add_argument("--timestamp", required=True, help="Timestamp used for signing")
    parser.add_argument("--user-id", required=True, help="userId used in signing and request body")
    parser.add_argument("--secret", required=True, help="Zoom LTI secret")
    parser.add_argument(
        "--param-order",
        default="key,timestamp,userId",
        choices=["key,timestamp,userId", "key,userId,timestamp"],
        help="Parameter order used for base string.",
    )
    args = parser.parse_args(argv)

    parts = build_lti_signature_parts(args.key, args.timestamp, args.user_id, args.param_order)
    base_string = build_lti_signature_base_string(parts)
    digest = build_lti_signature_digest(args.secret, base_string)
    standard_padded = b64encode(digest).decode("utf-8")
    urlsafe_padded = urlsafe_b64encode(digest).decode("utf-8")

    print(f"base_string={base_string}")
    print(f"sha1_digest_hex={digest.hex()}")
    print(f"standard_base64_padded={standard_padded}")
    print(f"standard_base64_unpadded={standard_padded.rstrip('=')}")
    print(f"urlsafe_base64_padded={urlsafe_padded}")
    print(f"urlsafe_base64_unpadded={urlsafe_padded.rstrip('=')}")
    print(f"variant_A_current={urlsafe_padded.rstrip('=')}")
    print(f"variant_B_standard_unpadded={standard_padded.rstrip('=')}")
    print(f"variant_C_urlsafe_padded={urlsafe_padded}")
    print(f"variant_D_standard_padded={standard_padded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
