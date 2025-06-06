# Test cases to validate functionality of the arp_update script

import logging
import pytest
import random

from tests.common.dualtor.mux_simulator_control import toggle_all_simulator_ports_to_rand_selected_tor  # noqa: F401
from tests.common.fixtures.ptfhost_utils import setup_vlan_arp_responder  # noqa: F401
from tests.common.helpers.assertions import pytest_assert as pt_assert
from tests.common.utilities import wait_until
from tests.common.dualtor.dual_tor_utils import mux_cable_server_ip

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.topology("t0")
]


@pytest.fixture
def setup(rand_selected_dut):
    cmds = [
        "docker exec swss supervisorctl stop arp_update",
        "ip neigh flush all"
    ]
    rand_selected_dut.shell_cmds(cmds)
    yield
    cmds[0] = "docker exec swss supervisorctl start arp_update"
    # rand_selected_dut.shell_cmds(cmds)


def neighbor_learned(dut, target_ip):
    neigh_output = dut.shell(f"ip neigh show {target_ip}")['stdout'].strip()
    logger.info(f"DUT neighbor entry: {neigh_output}")
    return neigh_output and ("REACHABLE" in neigh_output or "STALE" in neigh_output)


def appl_db_neighbor_syncd(dut, vlan_name, target_ip, exp_mac):
    asic_db_mac = dut.shell(f"sonic-db-cli APPL_DB hget 'NEIGH_TABLE:{vlan_name}:{target_ip}' 'neigh'")['stdout']
    logger.info(f"DUT neighbor mac: {asic_db_mac} of entry {vlan_name}:{target_ip}")
    return exp_mac.lower() == asic_db_mac.lower()


def ip_version_string(version):
    return f"ipv{version}"


@pytest.mark.parametrize("ip_version", [4, 6], ids=ip_version_string)
def test_kernel_asic_mac_mismatch(
    setup_standby_ports_on_non_enum_rand_one_per_hwsku_frontend_host_m_unconditionally,
    toggle_all_simulator_ports_to_rand_selected_tor,  # noqa: F811
    rand_selected_dut, ip_version, setup_vlan_arp_responder,  # noqa: F811
    tbinfo
):
    vlan_name, ipv4_base, ipv6_base, ip_offset = setup_vlan_arp_responder
    if 'dualtor' in tbinfo['topo']['name']:
        servers = mux_cable_server_ip(rand_selected_dut)
        intf = random.choice(list(servers))
        if ip_version == 4:
            target_ip = servers[intf]['server_ipv4'].split('/')[0]
        else:
            target_ip = servers[intf]['server_ipv6'].split('/')[0]
    else:
        if ip_version == 4:
            target_ip = ipv4_base.ip + ip_offset
        else:
            target_ip = ipv6_base.ip + ip_offset

    rand_selected_dut.shell(f"ping -c1 -W1 {target_ip}; true")

    wait_until(10, 1, 0, neighbor_learned, rand_selected_dut, target_ip)

    neighbor_info = rand_selected_dut.shell(f"ip neigh show {target_ip}")["stdout"].split()
    pt_assert(neighbor_info[2] == vlan_name)

    wait_until(5, 1, 0, appl_db_neighbor_syncd, rand_selected_dut, vlan_name, target_ip, neighbor_info[4])

    logger.info(f"Neighbor {target_ip} has been learned, APPL_DB and kernel are in sync")

    logger.info("Manually setting APPL_DB MAC address")
    rand_selected_dut.shell(
        f"sonic-db-cli APPL_DB hset 'NEIGH_TABLE:{vlan_name}:{target_ip}' 'neigh' '00:00:00:00:00:00'"
    )
    asic_db_mac = rand_selected_dut.shell(
        f"sonic-db-cli APPL_DB hget 'NEIGH_TABLE:{vlan_name}:{target_ip}' 'neigh'"
    )['stdout']
    pt_assert(neighbor_info[4].lower() != asic_db_mac.lower())
    logger.info("APPL_DB and kernel are out of sync (expected)")

    rand_selected_dut.shell("docker exec swss supervisorctl start arp_update")

    wait_until(10, 1, 0, lambda dut, ip: not neighbor_learned(dut, ip), rand_selected_dut, target_ip)
