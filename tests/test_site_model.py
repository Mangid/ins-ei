"""Basic tests for SITE_MODEL v0.1."""
from ins_ei.model import Component, Connection, SiteLocation, SiteModel, ThermalTopology


def test_site_accepts_optional_components():
    site = SiteModel(
        installation_id="pilot-001",
        location=SiteLocation(timezone="Europe/Vienna"),
        thermal_topology=ThermalTopology.BUFFER,
    )
    site.add_component(Component(id="grid", kind="GRID"))
    assert site.component("grid") is not None
    assert site.validate() == []


def test_invalid_connection_is_reported():
    site = SiteModel(
        installation_id="pilot-001",
        location=SiteLocation(timezone="Europe/Vienna"),
    )
    site.add_component(Component(id="buffer", kind="BUFFER"))
    site.connections.append(Connection(source="pellet", target="buffer"))
    assert "Unknown connection source: pellet" in site.validate()
