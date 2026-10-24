#!/usr/bin/env python3
"""Standalone BLE heart-rate receiver. Python 3.10+; pip install bleak==3.0.2.

Examples:
  python3 garmin_hr.py --scan
  python3 garmin_hr.py --device 'UUID-FROM-SCAN'
  python3 garmin_hr.py --device 'UUID-FROM-SCAN' --csv readings.csv

No controller imports, writes, or network endpoints. Ctrl-C disconnects.
"""
import argparse
import asyncio
import csv
from datetime import datetime, timezone
import json
import sys
import time
import uuid

SERVICE = '0000180d-0000-1000-8000-00805f9b34fb'
MEASUREMENT = '00002a37-0000-1000-8000-00805f9b34fb'


def parse_measurement(data):
    """Decode Bluetooth SIG Heart Rate Measurement; reject truncated packets."""
    if len(data) < 2:
        raise ValueError('Truncated heart-rate packet')
    flags = data[0]
    offset = 3 if flags & 1 else 2
    if len(data) < offset:
        raise ValueError('Truncated 16-bit heart rate')
    bpm = int.from_bytes(data[1:offset], 'little')
    contact = bool(flags & 2) if flags & 4 else None
    energy = None
    if flags & 8:
        if len(data) < offset + 2:
            raise ValueError('Truncated energy field')
        energy = int.from_bytes(data[offset:offset + 2], 'little')
        offset += 2
    rr = []
    if flags & 16:
        if len(data) == offset or (len(data) - offset) % 2:
            raise ValueError('Truncated RR intervals')
        rr = [int.from_bytes(data[i:i + 2], 'little') / 1024
              for i in range(offset, len(data), 2)]
    elif len(data) != offset:
        raise ValueError('Unexpected trailing bytes')
    return dict(hr=bpm, sensor_contact=contact, energy_expended=energy,
                rr_intervals_s=rr)


async def receive(args):
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError:
        raise RuntimeError('Install dependency: python3 -m pip install bleak==3.0.2')
    print('Scanning for broadcasting heart-rate sensors...', file=sys.stderr)
    found = await BleakScanner.discover(timeout=args.scan_seconds,
                                        return_adv=True, service_uuids=[SERVICE])
    candidates = list(found.values())
    for device, advert in candidates:
        print(f'{device.address}\t{advert.local_name or device.name or "Unnamed sensor"}',
              file=sys.stderr)
    if args.scan:
        if not candidates:
            print('No sensors found. Start Broadcast Heart Rate on the watch; check Bluetooth permission.',
                  file=sys.stderr)
        return
    matches = [(d, a) for d, a in candidates
               if d.address.casefold() == args.device.casefold()]
    if len(matches) != 1:
        raise RuntimeError('Selected device not found. Run --scan and copy its exact identifier.')
    device, advert = matches[0]
    disconnected = asyncio.Event()
    last_received = time.monotonic()
    session = str(uuid.uuid4())
    index = 0
    output = open(args.csv, 'x', newline='') if args.csv else None
    writer = csv.DictWriter(output, fieldnames=['timestamp', 'elapsed_s', 'hr',
                            'sensor_contact', 'sample_index', 'session_id']) if output else None
    if writer:
        writer.writeheader()
        output.flush()
    started = time.monotonic()

    def notification(_characteristic, data):
        nonlocal last_received, index
        try:
            measurement = parse_measurement(data)
        except ValueError as error:
            print(f'Ignored malformed packet: {error}', file=sys.stderr)
            return
        last_received = time.monotonic()
        index += 1
        record = dict(contract_version='garmin-hr/1', source='garmin_ble', simulated=False,
                      session_id=session, sample_index=index,
                      timestamp=datetime.now(timezone.utc).isoformat(),
                      elapsed_s=round(last_received - started, 3),
                      device_id=device.address, **measurement)
        # Arrival timestamp: the standard HR packet has no device sample timestamp.
        print(json.dumps(record), flush=True)
        if writer:
            writer.writerow({key: record[key] for key in writer.fieldnames})
            output.flush()

    try:
        async with BleakClient(device, disconnected_callback=lambda _: disconnected.set()) as client:
            await client.start_notify(MEASUREMENT, notification)
            print('Connected. Live JSON readings below; Ctrl-C to stop.', file=sys.stderr)
            while not disconnected.is_set():
                await asyncio.sleep(0.25)
                if time.monotonic() - last_received > args.stale_seconds:
                    raise RuntimeError('No fresh HR notifications; disconnected. Restart after checking the watch.')
                if args.duration and time.monotonic() - started >= args.duration:
                    return
            raise RuntimeError('Watch disconnected. No readings are being repeated or fabricated.')
    finally:
        if output:
            output.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--scan', action='store_true', help='List broadcasting HR sensors and exit')
    mode.add_argument('--device', help='Exact device identifier printed by --scan (UUID on macOS)')
    parser.add_argument('--csv', help='Create a NEW CSV file; existing files are never overwritten')
    parser.add_argument('--scan-seconds', type=float, default=10)
    parser.add_argument('--stale-seconds', type=float, default=10)
    parser.add_argument('--duration', type=float, default=0, help='Seconds to record after scanning; 0 = until Ctrl-C')
    args = parser.parse_args()
    if args.scan_seconds <= 0 or args.stale_seconds <= 0 or args.duration < 0:
        parser.error('Scan/stale times must be positive and duration nonnegative')
    try:
        asyncio.run(receive(args))
    except KeyboardInterrupt:
        print('Stopped.', file=sys.stderr)
    except Exception as error:
        print(f'Error: {error}\nOn Mac: System Settings > Privacy & Security > Bluetooth; allow your terminal/app.',
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
