"""Regression checks for the Admin Revenue API route registration."""

from server import app


def test_revenue_routes_are_registered():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/revenue" in paths
    assert "/api/revenue/employees" in paths
    assert "/api/revenue/agents" in paths
    assert "/api/revenue/customers" in paths
