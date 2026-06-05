"""Smoke tests: verify the package imports and exposes its public API."""

import prouter


def test_package_imports():
    assert prouter.__doc__


def test_graphbuilder_is_exported():
    assert "GraphBuilder" in prouter.__all__
    assert hasattr(prouter, "GraphBuilder")
