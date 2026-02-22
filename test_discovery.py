# test_discovery.py

import unittest
from unittest.mock import patch, MagicMock
from your_module import discover_devices

class TestDeviceDiscovery(unittest.TestCase):
    @patch('your_module.get_device_list')
    def test_discover_devices(self, mock_get_device_list):
        # Setup mock return value for device list
        mock_get_device_list.return_value = ['Device1', 'Device2', 'Device3']

        # Call the function we want to test
        devices = discover_devices()

        # Verify the devices discovered match the mocked list
        self.assertEqual(devices, ['Device1', 'Device2', 'Device3'])

    @patch('your_module.get_device_list')
    def test_discover_no_devices(self, mock_get_device_list):
        # Setup mock return value for no devices
        mock_get_device_list.return_value = []

        # Call the function we want to test
        devices = discover_devices()

        # Verify that the list is empty
        self.assertEqual(devices, [])

if __name__ == '__main__':
    unittest.main()