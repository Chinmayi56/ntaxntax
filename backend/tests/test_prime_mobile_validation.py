"""Regression tests for public Prime Service Indian mobile validation."""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("mongomock_motor")
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "ntaxco_prime_mobile_test")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")
os.environ.pop("NTAXCO_ENABLE_DEMO_SEED", None)

import motor.motor_asyncio as motor  # noqa: E402
motor.AsyncIOMotorClient = lambda *a, **k: AsyncMongoMockClient()
from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402


@pytest.mark.parametrize("value,expected", [
    ("9876543210", "9876543210"),
    ("8765432109", "8765432109"),
    ("7654321098", "7654321098"),
    ("6543210987", "6543210987"),
    ("+919876543210", "9876543210"),
    ("+91 9876543210", "9876543210"),
    ("98765 43210", "9876543210"),
    ("(987) 654-3210", "9876543210"),
    ("5876543210", None),
    ("987654321", None),
    ("98765432100", None),
    ("", None),
    ("+91+919876543210", None),
    ("98765abc10", None),
    ("91 9876543210", None),
    ("++919876543210", None),
])
def test_indian_mobile_normalization(value, expected):
    assert server._normalize_indian_mobile_number(value) == expected


def test_booking_normalizer_stores_only_normalized_mobile():
    name, mobile, service_id, gst = server._normalize_prime_booking({
        "customer_name": "Test Guest", "mobile_number": "+91 98765 43210",
        "service_id": "gst", "gst_number": ""
    })
    assert (name, mobile, gst) == ("Test Guest", "9876543210", "")


def test_booking_normalizer_rejects_invalid_mobile():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        server._normalize_prime_booking({"customer_name": "Test Guest", "mobile_number": "5876543210", "service_id": "gst"})
    assert exc.value.status_code == 422


def test_public_endpoint_accepts_valid_guest_and_returns_reference(monkeypatch):
    fake_result = {"booking_reference": "PS-TEST01", "mobile_number": "9876543210"}
    monkeypatch.setattr(server, "_insert_prime_booking", AsyncMock(return_value=fake_result))
    server._prime_guest_rate.clear()
    with TestClient(server.app) as client:
        response = client.post("/api/public/prime-service-bookings", json={
            "customer_name": "Test Guest", "mobile_number": "+91 9876543210",
            "gst_number": "", "service_id": "gst"
        })
    assert response.status_code == 201, response.text
    assert response.json()["booking_reference"] == "PS-TEST01"
    assert response.json()["mobile_number"] == "9876543210"


def test_public_endpoint_rejects_invalid_mobile_without_creating_booking(monkeypatch):
    insert = AsyncMock()
    monkeypatch.setattr(server, "_insert_prime_booking", insert)
    server._prime_guest_rate.clear()
    with TestClient(server.app) as client:
        response = client.post("/api/public/prime-service-bookings", json={
            "customer_name": "Test Guest", "mobile_number": "5876543210",
            "gst_number": "", "service_id": "gst"
        })
    assert response.status_code == 422, response.text
    assert "mobile number" in response.text.lower()
    insert.assert_not_awaited()
