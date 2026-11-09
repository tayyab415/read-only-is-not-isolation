import copy
import json
import unittest
from unittest.mock import patch

import service


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.state = {"containers": {"alice": "owned-alice"},
                      "networks": {"alice": "owned-net-alice"}}
        self.container = {"Image": "sha256:owned-image", "Mounts": [],
                          "Config": {"User": "20001:20001", "Env": ["PYTHON_VERSION=3.12"]},
                          "HostConfig": {"ReadonlyRootfs": True, "CapDrop": ["ALL"],
                                         "SecurityOpt": ["no-new-privileges:true"],
                                         "Memory": 134217728, "MemorySwap": 134217728,
                                         "NanoCpus": 500000000, "PidsLimit": 48, "Tmpfs": {}},
                          "NetworkSettings": {"Ports": {}, "Networks": {"owned-net-alice": {"IPAddress": "192.0.2.1"}}}}
        self.network = {"Internal": True, "Options": {
            "com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
            "Containers": {"id": {"Name": "owned-alice"}}}

    def readback(self):
        def command(args):
            if args[1] == "inspect":
                return json.dumps([self.container])
            return json.dumps([self.network])
        with patch.object(service.old, "load_state", return_value=self.state), \
                patch.object(service.old, "run_ok", side_effect=command):
            return service.boundary_readback("p2-owned")

    def test_expected_boundary_readback_passes(self):
        result = self.readback()
        self.assertFalse(result["containers"]["alice"]["forbidden_environment_present"])

    def test_credential_environment_and_wrong_resource_limits_fail(self):
        self.container["Config"]["Env"].append("AWS_SECRET_ACCESS_KEY=private-test")
        with self.assertRaises(RuntimeError):
            self.readback()
        self.container["Config"]["Env"].pop()
        self.container["HostConfig"]["PidsLimit"] = 0
        with self.assertRaises(RuntimeError):
            self.readback()

    def test_extra_network_host_mount_and_published_port_fail(self):
        self.container["NetworkSettings"]["Networks"]["foreign-net"] = {"IPAddress": "192.0.2.2"}
        with self.assertRaises(RuntimeError):
            self.readback()
        del self.container["NetworkSettings"]["Networks"]["foreign-net"]
        self.container["Mounts"] = [{"Type": "bind", "Destination": "/host", "RW": False}]
        with self.assertRaises(RuntimeError):
            self.readback()
        self.container["Mounts"] = []
        self.container["NetworkSettings"]["Ports"] = {"8000/tcp": [{"HostPort": "8000"}]}
        with self.assertRaises(RuntimeError):
            self.readback()

    def test_default_internal_bridge_is_not_accepted_as_isolated(self):
        del self.network["Options"]["com.docker.network.bridge.gateway_mode_ipv4"]
        with self.assertRaises(RuntimeError):
            self.readback()


if __name__ == "__main__":
    unittest.main()
