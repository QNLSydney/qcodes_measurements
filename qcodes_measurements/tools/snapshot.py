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
    return (list(snap['station']['instruments'].keys()) +
            list(snap['station']['components'].keys()))

def get_instr_snap(snap, instr):
    """
    Retrieve an instrument from a snapshot
    """
    try:
        instrs = snap['station']['instruments']
        return instrs[instr]
    except KeyError:
        instrs = snap['station']['components']
        return instrs[instr]

def extract_gate_desc(gate):
    """
    Import the location of a gate from the name of the gate channel
    """
    src, ch, *name = re.findall("([^_]+)", gate)
    name = "_".join(name)
    return src, ch, name

def pprint_dev_gates(snap, dev):
    """
    Print out gates from a snapshot, given the device name dev
    """
    GATE_ORDERS = ("T", "LW", "LP", "C", "RP", "RW", "N", "B")

    device = get_instr_snap(snap, dev)
    gates = device['parameters']

    ordered_gates = []
    for gate in gates:
        name = gate
        #_, _, name = extract_gate_desc(gate)
        for i, prefix in enumerate(GATE_ORDERS):
            if name.startswith(prefix) and name[-1].isnumeric():
                ordered_gates.append((name[-1], i, name, gate))
                break
            elif prefix in name:
                match = re.fullmatch(f"(.*){prefix}(.*)", name)
                ordered_gates.append(("".join(match.groups()), i, name, gate))
                break
        else:
            # If they aren't otherwise defined, add them to the end of the list
            ordered_gates.append(("10", 0, name, gate))
    ordered_gates.sort()

    output = []
    for _, _, name, gate in ordered_gates:
        voltage = gates[gate]['value']
        if voltage != 0:
            output.append((name, voltage))
    print(tabulate.tabulate(output,
                            headers=("Gate Name", "Voltage (V)"),
                            floatfmt=".3f"))
