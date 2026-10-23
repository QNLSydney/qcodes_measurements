# qcodes-measurements

Live plotting and measurement helpers for QCoDeS, using pyqtgraph in a separate
PySide6 process so plotting can continue while a measurement runs.

Requires Python 3.12 or newer. The current dependency baseline is QCoDeS 0.61.1,
PySide6 6.12, and pyqtgraph 0.14.

Install the project and development tools with uv:

```sh
uv sync
```

A measurement uses the usual QCoDeS parameters and experiment setup:

```python
from qcodes.dataset import initialise_or_create_database_at, new_experiment
from qcodes.parameters import Parameter
from qcodes_measurements import do1d

initialise_or_create_database_at("measurements.db")
new_experiment("example", sample_name="sample")

voltage = Parameter("voltage", unit="V", set_cmd=None, initial_value=0)
current = Parameter("current", unit="A", get_cmd=lambda: voltage() / 1e6)

dataset, windows, colorbars = do1d(
    voltage, -0.1, 0.1, 101, 0.01, current, save=False
)
window = windows[0]
```

`do0d`, `do1d`, `do2d`, and `sweep_time` accept `plot`, `plot_params`, `append`,
`stack`, `annotation`, and `save` options. With plotting enabled, the second return
value contains the live plot window. Use `plot=False` to keep QCoDeS's non-plotting
return values. Figures are saved to `figures/` by default; `save=False` disables
saving. `sweep_time` accepts a positive `delay` and an optional `until` duration in
seconds; omit `until` to measure until interrupted.

To plot an existing dataset, use `qcodes_measurements.plot.plot_dataset(dataset)`.
The bundled MDAC driver is available as `qcodes_measurements.drivers.MDAC` and
is used by all device wrappers.

`Device.add_parameter` follows QCoDeS's generic API: it accepts any
`ParameterBase` subclass and returns that subclass, including `Gate`, `Ohmic`,
and `DigitalGate`. Omitting `parameter_class`, or passing `None`, creates a
regular `Parameter`. Gates and ohmics also get channel controllers in
`device.gates` and `device.ohmics`; digital gates use `device.digital_gates`
on a `DigitalDevice`.

A QCoDeS 0.61.1 station config can instantiate an empty device:

```yaml
instruments:
  device:
    type: qcodes_measurements.device.Device
    init:
      label: Sample
```

After loading the DAC and device, wire up contacts in Python:

```python
from qcodes.station import Station
from qcodes_measurements.device import Device

station = Station(config_file="station.yaml")
mdac = station.load_instrument("mdac")  # Include the DAC's config in station.yaml.
device = station.load_instrument("device")
assert isinstance(device, Device)
device.add_gate("G1", mdac.ch01)
device.add_ohmic("S", mdac.ch02)
```

QCoDeS 0.61.1's native `add_parameters` section cannot configure these contact
wrappers. Its schema rejects a per-parameter `type` field, and its loader creates
a `DelegateParameter` whenever `source` is present. That source must resolve to
a **parameter within the instrument being configured**, rather than a channel
on another station instrument. Changing `Gate` to its fully qualified name, or
appending `.voltage` to `mdac.ch01`, does not remove these limitations.

To support contact definitions entirely in YAML, `Device` would need an explicit
configuration entry point, for example a new `init.contacts` constructor argument
containing the contact definitions. It would need to resolve source paths against
an explicitly supplied station or QCoDeS's instrument registry, select the
parameter class, and call `add_gate`, `add_ohmic`, or `add_digital_gate`. Sources
would have to be loaded first. The configuration layer would also need to preserve
safe ordering of connection states, digital IO modes, and initial voltages. Such an
`init.contacts` argument is a proposed extension and is not implemented here.
Supporting the original `add_parameters` layout would instead require extending
QCoDeS's station loader and schema, because source resolution happens before
`Device.add_parameter` is called and custom type options are not forwarded.

`Device` currently derives from `InstrumentBase`, so stations snapshot it under
`components`. It has no `Instrument` registration or `close` method, and does not
participate in station instrument revival, forced reconnection, or bulk instrument
cleanup. Full station lifecycle support would require deriving from `Instrument`
(and handling the standard `IDN` parameter) or providing a separate `Instrument`
adapter. Its controllers intentionally have their underlying DAC channels as
parents; changing the device's base class alone would not reparent them.

Run development checks with uv:

```sh
uv run ruff check qcodes_measurements tests
uv run ruff format --check qcodes_measurements tests
uv run ty check
uv run pylint qcodes_measurements
QT_QPA_PLATFORM=offscreen uv run python -m pytest -W error tests
```

`QT_QPA_PLATFORM=offscreen` runs Qt without displaying windows.
