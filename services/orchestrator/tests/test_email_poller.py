"""Email poller — the pure MIME-parsing helpers (IMAP/SMTP I/O is exercised live, not unit-tested)."""
from __future__ import annotations

from email.message import EmailMessage

from app.email_poller import _decode, _header, _plain_text
from app.notifier import clean_header, clean_secret, normalize_url


def test_normalize_url_prepends_scheme():
    assert normalize_url("hooks.slack.com/services/T/B/x") == "https://hooks.slack.com/services/T/B/x"
    assert normalize_url("https://hooks.slack.com/x") == "https://hooks.slack.com/x"
    assert normalize_url(" http://x.test ") == "http://x.test"
    assert normalize_url("") == ""


def test_clean_secret_strips_gmail_app_password_spaces():
    # Gmail shows App Passwords as 4 space-separated groups (often with non-breaking spaces) but they
    # must be sent with NO whitespace, and \xa0 crashes the ASCII IMAP/SMTP login — strip it all.
    assert clean_secret("abcd efgh ijkl mnop") == "abcdefghijklmnop"
    assert clean_secret("abcd\xa0efgh\xa0ijkl\xa0mnop") == "abcdefghijklmnop"
    assert clean_secret("  token  ") == "token"


def test_clean_header_removes_nonbreaking_space():
    assert clean_header("Shipwright\xa0<a@b.com>") == "Shipwright <a@b.com>"
    assert clean_header("  x  ") == "x"


def test_header_decodes_rfc2047():
    # "Zaïn" MIME-encoded — must come back as unicode, not the raw =?utf-8?… token
    assert _header("=?utf-8?q?Za=C3=AFn?= <a@b.com>") == "Zaïn <a@b.com>"
    assert _header(None) == ""


def test_plain_text_from_simple_message():
    msg = EmailMessage()
    msg.set_content("approve SW-142\n\n> quoted original")
    assert "approve SW-142" in _plain_text(msg)


def test_plain_text_prefers_text_part_of_multipart():
    msg = EmailMessage()
    msg.set_content("approve SW-9")                       # text/plain
    msg.add_alternative("<p>approve SW-9</p>", subtype="html")  # text/html
    body = _plain_text(msg)
    assert "approve SW-9" in body
    assert "<p>" not in body  # the HTML alternative is not what we return


def test_decode_handles_missing_charset():
    msg = EmailMessage()
    msg.set_content("hello")
    assert "hello" in _decode(msg)
