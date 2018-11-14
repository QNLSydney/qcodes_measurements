"""
Tools for getting data from a snapshot
"""

import json
import re
import tabulate

from qcodes.dataset.data_set import load_by_id

def get_snapshot(data_id):
    """
    Get a snapshot from the dataset indexed by data_id
    """
    data = load_by_id(data_id)
    snap_str = data.get_metadata('snapshot')
    snap = json.loads(snap_str)
    return snap

def list_instruments(snap):
    """
    List instruments from a snapshot
    """
    return list(snap['station']['instruments'].keys())

def get_instr_snap(snap, instr):
    """
    Retrieve an instrument from a snapshot
    """
    instrs = snap['station']['instruments']
    return instrs[instr]

def extract_gate_desc(gate):
    """
    Import the location of a gate from the name of the gate channel
    """
    src, ch, name = re.findall("([^_]+)", gate)
    return src, ch, name

def pprint_dev_gates(snap, dev):
    """
    Print out gates from a snapshot, given the device name dev
    """
    GATE_ORDERS = ("LW", "LP", "C", "RP", "RW", "N")

    device = get_instr_snap(snap, dev)
    gates = device['submodules']['gates']['channels']

    ordered_gates = []
    for gate in gates:
        _, _, name = extract_gate_desc(gate)
        for i, prefix in enumerate(GATE_ORDERS):
            if name.startswith(prefix):
                ordered_gates.append((int(name[-1]), i, name, gate))
                break
        else:
            # If they aren't otherwise defined, add them to the end of the list
            ordered_gates.append((10, 0, name, gate))
    ordered_gates.sort()

    output = []
    for _, _, name, gate in ordered_gates:
        voltage = gates[gate]['parameters']['voltage']['value']
        if voltage != 0:
            output.append((name, voltage))
    print(tabulate.tabulate(output,
                            headers=("Gate Name", "Voltage (V)"),
                            floatfmt=".3f"))
