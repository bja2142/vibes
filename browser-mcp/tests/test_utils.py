from browser_puppet.utils import compute_totp, summarize_text


def test_compute_totp_matches_known_rfc_vector() -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    code = compute_totp(secret, digits=8, period=30, algorithm="SHA1", for_time=59)

    assert code == "94287082"


def test_summarize_text_trims_and_collapses_whitespace() -> None:
    value = "alpha   beta\n gamma"

    assert summarize_text(value, limit=20) == "alpha beta gamma"
