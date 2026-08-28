"""Pytest-only authenticated fixture for the isolated v5.1 shadow transport test.

Production authentication is never disabled.  The fixture configures the same
QA credentials used by the canonical tests and signs the test client in via
the real ``/login`` route before the shadow endpoint is asserted.
"""

from __future__ import annotations

import os

import pytest


def pytest_configure() -> None:
    os.environ.setdefault("QUANT_AGENT_USER", "qa-user")
    os.environ.setdefault("QUANT_AGENT_PASSWORD", "qa-password")
    os.environ.setdefault("QUANT_AGENT_SECRET", "qa-secret-only")


@pytest.fixture(autouse=True)
def authenticate_v51_shadow_transport(request):
    test_class = request.cls
    if test_class is None or test_class.__name__ != "AssetAllocationV51ShadowTransportTest":
        yield
        return
    response = test_class.client.post(
        "/login",
        data={
            "username": os.environ["QUANT_AGENT_USER"],
            "password": os.environ["QUANT_AGENT_PASSWORD"],
        },
    )
    assert response.status_code in {302, 303}
    yield
