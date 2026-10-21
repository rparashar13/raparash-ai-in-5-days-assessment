"""Outcome-based security test suite for the Shopping Assistant ADK 2.0 agent."""

import pytest

from app.agent import DISCOUNT_STORE, redeem_discount


@pytest.fixture(autouse=True)
def reset_store():
    """Ensure strict test isolation by resetting in-memory store state before each test run."""
    DISCOUNT_STORE["WELCOME50"] = False
    DISCOUNT_STORE["SUMMER20"] = False
    yield
    DISCOUNT_STORE["WELCOME50"] = False
    DISCOUNT_STORE["SUMMER20"] = False


def test_discount_code_can_only_be_redeemed_once():
    """Verify a user cannot submit a request reusing a single-use code."""
    # First redemption - should succeed
    res_one = redeem_discount("WELCOME50", "user_123")
    assert "Success" in res_one
    assert DISCOUNT_STORE["WELCOME50"] is True

    # Second redemption trying to reuse the same code
    res_two = redeem_discount("WELCOME50", "user_456")
    assert "Error: Discount code has already been redeemed" in res_two
    assert DISCOUNT_STORE["WELCOME50"] is True


def test_discount_redemption_rejects_invalid_code():
    """Verify that unknown discount codes are hard-blocked."""
    res = redeem_discount("INVALID999", "user_123")
    assert "Error: Invalid discount code" in res
    assert DISCOUNT_STORE["WELCOME50"] is False
    assert DISCOUNT_STORE["SUMMER20"] is False


def test_discount_redemption_rejects_guest_accounts():
    """Verify that unauthenticated guest accounts cannot redeem discounts."""
    res = redeem_discount("SUMMER20", "guest_999")
    assert "Error: Registered user account required" in res
    assert DISCOUNT_STORE["SUMMER20"] is False


def test_discount_redemption_rejects_case_varied_guest_and_empty_user():
    """Verify guest prefix check is case-insensitive and empty user IDs are rejected."""
    res_upper = redeem_discount("SUMMER20", "GUEST_123")
    assert "Error: Registered user account required" in res_upper
    assert DISCOUNT_STORE["SUMMER20"] is False

    res_empty = redeem_discount("SUMMER20", "   ")
    assert "Error:" in res_empty
    assert DISCOUNT_STORE["SUMMER20"] is False
