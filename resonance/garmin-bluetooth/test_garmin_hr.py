"""Offline protocol tests; no Bluetooth hardware or Bleak dependency needed."""
import unittest
from garmin_hr import parse_measurement


class PacketTests(unittest.TestCase):
    def test_uint8(self):
        self.assertEqual(parse_measurement(bytes([0, 72]))['hr'], 72)

    def test_uint16(self):
        self.assertEqual(parse_measurement(bytes([1, 44, 1]))['hr'], 300)

    def test_contact_unknown_and_absent(self):
        self.assertIsNone(parse_measurement(bytes([0, 72]))['sensor_contact'])
        self.assertFalse(parse_measurement(bytes([4, 72]))['sensor_contact'])
        self.assertTrue(parse_measurement(bytes([6, 72]))['sensor_contact'])

    def test_energy_and_rr(self):
        result = parse_measurement(bytes([30, 80, 10, 0, 0, 4, 0, 2]))
        self.assertEqual(result['energy_expended'], 10)
        self.assertEqual(result['rr_intervals_s'], [1.0, 0.5])

    def test_reject_malformed(self):
        for packet in [b'', b'\x00', b'\x01\x48', b'\x08\x48',
                       b'\x10\x48', b'\x10\x48\x00', b'\x00\x48\x01']:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                parse_measurement(packet)


if __name__ == '__main__':
    unittest.main()
