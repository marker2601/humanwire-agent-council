from secondsignal.redaction import redact_sensitive


def test_redacts_otp_recovery_code_and_bearer_token() -> None:
    text = "OTP 449102 recovery code AB12-CD34 token Bearer abc.def.ghi"

    result = redact_sensitive(text)

    assert "449102" not in result
    assert "AB12-CD34" not in result
    assert "abc.def.ghi" not in result
    assert result.count("[REDACTED]") == 3


def test_preserves_currency_amounts() -> None:
    assert "$500" in redact_sensitive("Please send $500 today")
