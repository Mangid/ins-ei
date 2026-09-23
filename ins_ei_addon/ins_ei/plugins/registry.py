"""Runtime registry for manufacturer plugins."""
from __future__ import annotations
from .base import DevicePlugin

class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, DevicePlugin] = {}

    def register(self, plugin: DevicePlugin) -> None:
        if plugin.plugin_id in self._plugins:
            raise ValueError(f"duplicate plugin id: {plugin.plugin_id}")
        self._plugins[plugin.plugin_id] = plugin

    def get(self, plugin_id: str) -> DevicePlugin | None:
        return self._plugins.get(plugin_id)

    def all(self) -> list[DevicePlugin]:
        return list(self._plugins.values())
