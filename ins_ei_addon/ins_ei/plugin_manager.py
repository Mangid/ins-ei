"""Plugin registry and per-installation instances."""
from dataclasses import dataclass
from typing import Callable
from .plugin_sdk import PluginInstance,PluginManifest
Factory=Callable[[str,dict],PluginInstance]
@dataclass
class RegisteredPlugin: manifest:PluginManifest;factory:Factory
class PluginManager:
    def __init__(self):self._registry={};self._instances={}
    def register(self,manifest, factory):self._registry[manifest.plugin_id]=RegisteredPlugin(manifest,factory)
    def available(self):return [x.manifest for x in self._registry.values()]
    def create(self,plugin_id,instance_id,config):
        if instance_id in self._instances:raise ValueError(f"PLUGIN_INSTANCE_EXISTS:{instance_id}")
        obj=self._registry[plugin_id].factory(instance_id,config);self._instances[instance_id]=obj;return obj
    def instances(self):return dict(self._instances)
    def get(self,instance_id):return self._instances[instance_id]

def builtin_manager():
    from .plugins.oekofen_v2 import MANIFEST as OEKOFEN_MANIFEST,factory as oekofen_factory
    manager=PluginManager();manager.register(OEKOFEN_MANIFEST,oekofen_factory)
    return manager
