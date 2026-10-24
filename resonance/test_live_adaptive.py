"""Live-mode protocol tests using fake BLE packets and a controlled clock."""


import json


import math


import sys


from pathlib import Path


import pytest


sys.path.insert(0, str(Path(__file__).parent))


import controller as c


import garmin_source as g


from adaptive_adapter import AdaptiveAdapter


from test_adaptive_integration import Player, track


def test_one_ble_packet_cannot_be_used_twice():
    source = g.GarminSource(connect=False)
    source.on_packet(bytes([6, 72]))
    assert source.next() == 72
    assert math.isnan(source.next())


def test_reconnect_does_not_switch_to_another_watch(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    source = g.GarminSource(connect=False)
    connections = []
    scans = []
    async def discover(**kwargs):
        scans.append(1)
        address = 'watch-A' if len(scans) == 1 else 'watch-B'
        if len(scans) == 2:
            source.close()
        return {address:(SimpleNamespace(address=address, name=address), SimpleNamespace(local_name=address))}
    class Client:
        def __init__(self, device, disconnected_callback):
            connections.append(device.address)
            self.disconnected = disconnected_callback
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def start_notify(self, *_):
            self.disconnected(self)
    async def no_wait(_):
        pass
    monkeypatch.setitem(sys.modules, 'bleak', SimpleNamespace(BleakClient=Client,
                        BleakScanner=SimpleNamespace(discover=discover)))
    monkeypatch.setattr(g.asyncio, 'sleep', no_wait)
    asyncio.run(source._loop())
    assert connections == ['watch-A']
    assert source.status()['device_id'] == 'watch-A'

